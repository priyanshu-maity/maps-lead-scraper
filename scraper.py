import re
import os
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


class MapsLeadScraper:
    def __init__(self,
                 business_type: str,
                 location: str,
                 output_path: str,
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
                    f"location={location}, output_path={output_path}, "
                    f"headless={headless}",
            category='INFO'
        )

        # Core configuration
        self.url: str | None = None
        self.business_type: str = business_type
        self.location: str = location
        self.output_path: str = output_path
        self.headless: bool = headless

        # Load selectors from YAML configuration
        self.selectors: dict[str, str] = self.load_selectors()

        # Data storage
        self.data: list[dict] = []

        # Initialize driver
        self.driver = None

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
                incognito=False,
                page_load_strategy='normal'
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

    def run(self):
        try:
            self.build_search_url()
            self.driver = self.get_driver()

        except Exception as e:
            self.logger.log(
                message="Critical error in main execution flow",
                category='CRITICAL',
                exception=e
            )
            raise
        finally:
            if self.driver:
                self.driver.quit()
                self.logger.log(
                    message="Driver closed successfully",
                    category='INFO'
                )

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

    def save_to_csv(self, filename: str):
        if not self.data:
            self.logger.log(
                message="No data to save",
                category='WARNING'
            )
            return

        output_file = os.path.join(self.output_path, filename)
        df = pd.DataFrame(self.data)
        df.to_csv(output_file, index=False)

        self.logger.log(
            message=f"Data saved to {output_file}",
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

    def get_results_container(self) -> WebElement:
        feed_xpath = self.selectors['results_feed']['xpath']
        locator = (By.XPATH, feed_xpath)
        self.logger.log(
            message="Locating results container",
            category='INFO'
        )
        return self.wait_for_element(locator, timeout=15)

    def scroll_results_feed(self) -> None:
        feed = self.get_results_container()
        last_height = 0

        while True:
            current_height = self.driver.execute_script(
                "return arguments[0].scrollHeight",
                feed
            )
            if current_height == last_height:
                break
            self.driver.execute_script(
                "arguments[0].scrollTo(0, arguments[0].scrollHeight)",
                feed
            )
            last_height = current_height
            self.driver.implicitly_wait(2)

    def get_listing_links(self) -> list[str]:
        feed = self.get_results_container()
        links_xpath = self.selectors['listing_links']['xpath']
        anchors = feed.find_elements(
            By.XPATH,
            links_xpath
        )
        links = []

    @staticmethod
    def load_selectors() -> dict[str, str]:
        with open('selectors.yaml', 'r') as file:
            config = yaml.safe_load(file)
            return config


if __name__ == "__main__":
    scraper = MapsLeadScraper(
        business_type="real estate agencies",
        location="New York City",
        output_path="./output",
        logs_path=Path("./logs"),
        headless=False
    )

    # Run the scraper
    scraper.run()
