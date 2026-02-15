import re
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import yaml
import pandas as pd

from seleniumbase import Driver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    ElementClickInterceptedException,
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

        # Initialize logger
        self.logger: Logging = Logging(
            script_name='maps_lead_scraper',
            color_logs=True,
            log_dir=logs_path
        )
        self.logger.log(
            message=f"Initializing scraper with business_type={business_type}, "
                    f"location={location}, sheet_id={sheet_id}, "
                    f"headless={headless}",
            category='INFO'
        )

        # Core configuration
        self.url: str | None = None
        self.business_type: str = business_type
        self.location: str = location
        self.sheet_id: str = sheet_id
        self.headless: bool = headless

        self.fields = ['business_name', 'business_type', 'address', 'phone', 'website', 'email', 'maps_url']

        # Load selectors from YAML configuration
        self.selectors: dict[str, str] = self.load_selectors()

        # Data storage
        self.writer = GSheetBatchWriter('creds.json', self.sheet_id, headers=self.fields, dedupe_on=['maps_url'])

        # Initialize driver
        self.driver = None

    def run(self):
        try:
            self.build_search_url()
            self.driver = self.get_driver()
            self.execute_scraping_workflow()
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

    def get_driver(self):
        try:
            self.logger.log(
                message="Setting up SeleniumBase Driver",
                category='INFO'
            )

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
                page_load_strategy='eager',
                block_images=True,
            )

            driver.set_page_load_timeout(30)

            self.logger.log(
                message=f"Finished setting up SeleniumBase Driver"
                        f"{' in headless mode' if self.headless else ''} "
                        f"with profile at {profile_path}",
                category='INFO'
            )

        except Exception as e:
            self.logger.log(
                message="Error while setting up SeleniumBase driver",
                category='CRITICAL',
                exception=e
            )
            raise SystemExit("Stopping scraper due to driver setup failure")

        self.logger.log(
            message=f"Fetching website: {self.url}",
            category='INFO'
        )

        retries = 3
        while retries > 0:
            try:
                driver.get(self.url)
                self.logger.log(
                    message="Successfully fetched website",
                    category='INFO'
                )
                return driver
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

        return None

    def execute_scraping_workflow(self) -> None:
        listing_links = self.get_listing_links()

        self.logger.log(
            message=f"Collected {len(listing_links)} listing URLs",
            category="INFO"
        )

        self.scrape_all_listings(listing_links)

    def scrape_all_listings(self, links: list[str]) -> None:
        for index, url in enumerate(links, start=1):
            listing_data = {"maps_url": url}
            try:
                self.logger.log(
                    message=f"Opening listing {index}/{len(links)}: {url}",
                    category="INFO"
                )
                self.driver.get(url)

                self.wait_for_element(
                    (By.XPATH, self.selectors['business_name']),
                    timeout=15
                )

                listing_data.update(self.extract_listing_parameters())
                self.writer.dump(listing_data)

            except Exception as e:
                self.logger.log(
                    message=f"Failed scraping listing: {url}",
                    category="ERROR",
                    exception=e
                )

    def extract_listing_parameters(self) -> dict:
        name = self.safe_find_element(
            (By.XPATH, self.selectors["business_name"])
        )

        business_type = self.safe_find_element(
            (By.XPATH, self.selectors["business_type"])
        )

        address = self.safe_find_element(
            (By.XPATH, self.selectors["business_address"])
        )

        phone = self.safe_find_element(
            (By.XPATH, self.selectors["business_phone"])
        )
        website = ""
        try:
            element = self.driver.find_element(
                By.XPATH,
                self.selectors["business_website"]
            )
            website = element.get_attribute("href") or ""
        except NoSuchElementException:
            pass

        return {
            "business_name": name,
            "business_type": business_type,
            "address": address,
            "phone": phone,
            "website": website
        }

    def get_listing_links(self) -> list[str]:
        feed = self.get_results_container()
        links_xpath = self.selectors['listing_links']
        anchors = feed.find_elements(
            By.XPATH,
            links_xpath
        )

        links = []

        for anchor in anchors:
            try:
                listing_container = anchor.find_element(By.XPATH, self.selectors['listing_containers'])
                if self.is_sponsored(listing_container):
                    continue

                href = anchor.get_attribute("href")

                if href and href not in seen:
                    seen.add(href)
                    links.append(href)

            except NoSuchElementException:
                continue

        self.logger.log(
            message=f"Collected {len(links)} organic listing URLs",
            category="INFO"
        )

        return links

    def get_results_container(self) -> WebElement:
        feed_xpath = self.selectors['results_feed']
        locator = (By.XPATH, feed_xpath)
        self.logger.log(
            message="Locating results container",
            category='INFO'
        )
        return self.wait_for_element(locator, timeout=15)

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

    def wait_for_element(self,
                         locator: tuple,
                         timeout: int = 10,
                         condition=EC.presence_of_element_located) -> WebElement:
        try:
            element = WebDriverWait(self.driver, timeout).until(
                condition(locator)
            )
            return element
        except TimeoutException as e:
            self.logger.log(
                message=f"Timeout waiting for element: {locator}",
                category='ERROR',
                exception=e
            )
            raise

    @staticmethod
    def load_selectors() -> dict[str, str]:
        with open('selectors.yaml', 'r') as file:
            config = yaml.safe_load(file)
            return config


if __name__ == "__main__":
    scraper = MapsLeadScraper(
        business_type="pet shop",
        location="New York City",
        sheet_id="137B0pNDLA6vIa6J7IHxlZoMmk8Pe1tKAfcHMprC-xrc",
        logs_path=Path("./logs"),
        headless=False
    )

    # Run the scraper
    scraper.run()
