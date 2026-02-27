import inspect
import os
import time
from pathlib import Path
from urllib.parse import quote_plus

import yaml

from seleniumbase import Driver
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException
)

from scraperlog import Logging
from batch_writer import GSheetBatchWriter


class MapsLeadScraper:
    def __init__(self,
                 business_type: str,
                 location: str,
                 sheet_id: str,
                 logs_path: Path,
                 headless: bool = True):

        self.url: str | None = None
        self.business_type: str = business_type
        self.location: str = location
        self.sheet_id: str = sheet_id
        self.logs_path: Path = logs_path
        self.headless: bool = headless

        self.fields = ['business_name', 'business_type', 'address', 'phone', 'website', 'email', 'maps_url']
        self.init_params = locals()

        self.logger: Logging | None = None
        self.selectors: dict[str, str] | None = None
        self.writer: GSheetBatchWriter | None = None
        self.driver: WebDriver | None = None

        self.setup()
    
    def setup(self):
        # Initialize logger
        self.logger = Logging(
            script_name='maps_lead_scraper',
            color_logs=True,
            log_dir=self.logs_path
        )

        self.logger.log(message=f"Setting up scraper with parameters: {self._format_init_params(self.init_params)}",
                        category='INFO')

        # Load selectors
        try:
            self.logger.log(message="Loading selectors",
                            category='INFO')

            self.selectors: dict[str, str] = self.load_selectors()

            self.logger.log(message="Loaded selectors successfully",
                            category='INFO')
        except Exception as e:
            self.logger.log(
                message="Error loading selectors",
                category='CRITICAL',
                exception=e
            )
            raise SystemExit("Stopping scraper due to selector load failure")

        # Initialize Google Sheets writer
        try:
            self.logger.log(message="Initializing Google Sheets writer",
                            category='INFO')

            self.writer = GSheetBatchWriter(
                creds_path='creds.json',
                sheet_id=self.sheet_id,
                headers=self.fields,
                dedupe_on=['maps_url']
            )

            self.logger.log(message="Initialized Google Sheets writer successfully",
                            category='INFO')
        except Exception as e:
            self.logger.log(
                message="Error initializing Google Sheets writer",
                category='CRITICAL',
                exception=e
            )
            raise SystemExit("Stopping scraper due to writer initialization failure")

        # Initialize Selenium driver
        try:
            self.logger.log(message="Initializing webdriver",
                            category='INFO')

            self.driver = self.init_driver()

            self.logger.log(message="Initialized webdriver successfully",
                            category='INFO')
        except Exception as e:
            self.logger.log(
                message="Error initializing webdriver",
                category='CRITICAL',
                exception=e
            )
            raise SystemExit("Stopping scraper due to driver initialization failure")

        self.logger.log(
            message="Setup complete, ready to run scraper",
            category='INFO'
        )

    def run(self):
        try:
            self.build_search_url()
            self.get_driver()
            self.parse()
            self.writer.flush()

        except Exception as e:
            self.logger.log(
                message="Critical error in main execution flow",
                category='CRITICAL',
                exception=e
            )
            raise
        finally:
            self.writer.flush()
            if self.driver:
                self.driver.quit()
                self.logger.log(
                    message="Driver closed successfully",
                    category='INFO'
                )

    def build_search_url(self) -> str:
        query = f"{self.business_type} in {self.location}"
        encoded_query = quote_plus(query)
        self.url = f"https://www.google.com/maps/search/{encoded_query}"

        self.logger.log(
            message=f"Built search URL: {self.url}",
            category="DEBUG"
        )

        return self.url

    def init_driver(self) -> WebDriver:
        try:
            profile_path = os.path.join(os.getcwd(), 'chrome_profile')
            os.makedirs(profile_path, exist_ok=True)

            driver = Driver(
                browser='chrome',
                uc=True,
                headless=self.headless,
                user_data_dir=profile_path,
                no_sandbox=True,
                disable_gpu=True,
                incognito=True,
                page_load_strategy='normal',
            )

            driver.set_page_load_timeout(30)

            self.logger.log(
                message=f"Finished setting up SeleniumBase Driver"
                        f"{' in headless mode' if self.headless else ''} "
                        f"with profile at {profile_path}",
                category='INFO'
            )

            return driver
        except Exception as e:
            self.logger.log(
                message="Error while setting up SeleniumBase driver",
                category='CRITICAL',
                exception=e
            )
            raise SystemExit("Stopping scraper due to driver setup failure")

    def get_driver(self):
        self.logger.log(
            message=f"Fetching website: {self.url}",
            category='INFO'
        )

        retries = 3
        while retries > 0:
            try:
                self.driver.get(self.url)
                self.logger.log(
                    message="Successfully fetched website",
                    category='INFO'
                )
            except Exception as e:
                retries -= 1
                if retries > 0:
                    self.logger.log(
                        message=f"Error while fetching website. Retrying... ({retries} attempts left)",
                        category='ERROR',
                        exception=e
                    )
                else:
                    self.logger.log(
                        message="Error fetching website. Ran out of retries.",
                        category='CRITICAL',
                        exception=e
                    )
                    raise SystemExit("Stopping scraper due to website fetch failure")

    def parse(self) -> None:
        listing_links = self.parse_listings()

        self.logger.log(
            message=f"Collected {len(listing_links)} listing URLs",
            category="INFO"
        )

        self.parse_listing_page(listing_links)

    def parse_listings(self, threshold: int | None = None) -> set[str]:
        feed = WebDriverWait(self.driver, timeout=15).until(
            EC.presence_of_element_located((By.XPATH, self.selectors['results_feed']))
        )

        listing_links: set[str] = set()
        results_count: int = 0

        while threshold is None or len(listing_links) < threshold:
            try:
                listing_anchors = WebDriverWait(self.driver, timeout=5).until(
                    lambda d: elems if len(elems := d.find_elements(By.XPATH, self.selectors[
                        'listing_anchors'])) > results_count else False
                )

            except TimeoutException:
                self.logger.log(
                    message="Timeout waiting for listing anchors. Assuming no more results to load.",
                    category='INFO'
                )
                break

            try:
                if len(listing_anchors) == results_count:
                    self.logger.log(
                        message=f"No new listing anchors found after waiting.",
                        category='INFO'
                    )
                    break

                new_results_count = len(listing_anchors) - results_count
                results_count += new_results_count
                listing_anchors = listing_anchors[-new_results_count:]

                for listing_anchor in listing_anchors:
                    try:
                        listing_container = listing_anchor.find_element(
                            By.XPATH,
                            self.selectors['listing_container']
                        )

                        if self.is_sponsored(listing_container):
                            continue

                        href = listing_anchor.get_attribute('href')
                        if href and 'maps/place' in href:
                            listing_links.add(href)

                        if (threshold is not None) and (len(listing_links) >= threshold):
                            break

                    except Exception as e:
                        self.logger.log(
                            message="Error processing a listing anchor",
                            category="ERROR",
                            exception=e
                        )

                current_count = len(listing_links)
                self.logger.log(
                    message=f"Collected {current_count} organic listing URLs",
                    category="INFO"
                )

                if (threshold is not None) and (current_count >= threshold):
                    break

                self.driver.execute_script(
                    "arguments[0].scrollTop = arguments[0].scrollHeight",
                    feed
                )

            except Exception as e:
                self.logger.log(
                    message="Error during link collection",
                    category="ERROR",
                    exception=e
                )

        return listing_links

    def parse_listing_page(self, links: set[str]) -> None:
        for index, url in enumerate(links, start=1):
            listing_data = {"maps_url": url}
            max_retries = 3

            for attempt in range(max_retries):
                try:
                    self.logger.log(
                        message=f"Opening listing {index}/{len(links)} (attempt {attempt + 1}): {url}",
                        category="INFO"
                    )

                    self.driver.execute_script("window.stop();")
                    self.driver.get(url)

                    WebDriverWait(self.driver, 20).until(
                        lambda d: d.execute_script("return document.readyState") == "complete"
                    )

                    self.driver.execute_script("window.scrollTo(0, 500);")
                    time.sleep(1)
                    self.driver.execute_script("window.scrollTo(0, 0);")
                    time.sleep(2)

                    business_name_locator = (By.XPATH, self.selectors['business_name'])
                    element = WebDriverWait(self.driver, 15).until(
                        EC.presence_of_element_located(business_name_locator)
                    )

                    WebDriverWait(self.driver, 10).until(
                        lambda d: d.find_element(*business_name_locator).text.strip() != ""
                    )

                    time.sleep(1)

                    listing_data.update(self.extract_listing_parameters())

                    if not listing_data.get("business_name") or listing_data["business_name"] == "":
                        raise ValueError("Business name is empty")

                    self.writer.dump(listing_data)
                    break

                except Exception as e:
                    if attempt < max_retries - 1:
                        self.logger.log(
                            message=f"Attempt {attempt + 1} failed, retrying...",
                            category="WARNING",
                            exception=e
                        )
                        time.sleep(2)
                    else:
                        self.logger.log(
                            message=f"Failed scraping listing after {max_retries} attempts: {url}",
                            category="ERROR",
                            exception=e
                        )

    def extract_listing_parameters(self) -> dict:
        def get_text(xpath: str, attribute: str = None) -> str:
            for attempt in range(3):
                try:
                    element = self.driver.find_element(By.XPATH, xpath)

                    # Method 1: Regular text
                    text = element.text.strip()
                    if text:
                        return text

                    # Method 2: innerText via JS
                    text = self.driver.execute_script("return arguments[0].innerText;", element)
                    if text and text.strip():
                        return text.strip()

                    # Method 3: textContent via JS
                    text = self.driver.execute_script("return arguments[0].textContent;", element)
                    if text and text.strip():
                        return text.strip()

                    # Method 4: Attribute
                    if attribute:
                        text = element.get_attribute(attribute)
                        if text and text.strip():
                            return text.strip()

                    if attempt < 2:
                        time.sleep(1)

                except NoSuchElementException:
                    if attempt < 2:
                        time.sleep(1)
                    else:
                        return ""

            return ""

        try:
            name = get_text(self.selectors["business_name"])
            business_type = get_text(self.selectors["business_type"])
            address = get_text(self.selectors["business_address"])
            phone = get_text(self.selectors["business_phone"])

            website = ""
            try:
                element = self.driver.find_element(By.XPATH, self.selectors["business_website"])
                website = element.get_attribute("href") or ""
                if not website:
                    website = element.get_attribute("data-href") or ""
            except NoSuchElementException:
                pass

            result = {
                "business_name": name,
                "business_type": business_type,
                "address": address,
                "phone": phone,
                "website": website
            }

            self.logger.log(
                message=f"Extracted: {result}",
                category="DEBUG"
            )

            return result

        except Exception as e:
            self.logger.log(
                message="Error extracting listing parameters",
                category="ERROR",
                exception=e
            )
            return {
                "business_name": "",
                "business_type": "",
                "address": "",
                "phone": "",
                "website": ""
            }

    def is_sponsored(self, element: WebElement) -> bool:
        try:
            sponsored_elements = element.find_elements(
                By.XPATH,
                self.selectors['sponsor_badge']
            )
            return len(sponsored_elements) > 0
        except Exception as e:
            self.logger.log(
                message="Error while checking sponsored badge",
                category="WARNING",
                exception=e
            )
            return False

    def safe_find_element(self,
                          locator: tuple,
                          default: str = '') -> str:
        try:
            element = self.driver.find_element(*locator)
            return element.text.strip()
        except NoSuchElementException:
            self.logger.log(
                message=f"Element not found: {locator}. Using default: '{default}'",
                category='WARNING'
            )
            return default

    def _format_init_params(self, local_vars: dict) -> str:
        sig = inspect.signature(self.__init__)
        parameters = list(sig.parameters)[1:]

        return ', '.join(
            f'{name}={local_vars[name]}'
            for name in parameters
        )

    @staticmethod
    def load_selectors() -> dict[str, str]:
        with open('selectors.yaml', 'r') as file:
            config = yaml.safe_load(file)
            return config


if __name__ == "__main__":
    scraper = MapsLeadScraper(
        business_type="real estate agencies",
        location="New York City",
        sheet_id="137B0pNDLA6vIa6J7IHxlZoMmk8Pe1tKAfcHMprC-xrc",
        logs_path=Path("./logs"),
        headless=False
    )

    # Run the scraper
    scraper.run()
