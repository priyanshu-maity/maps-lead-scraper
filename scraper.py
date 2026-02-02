import re
import os
from datetime import datetime
from pathlib import Path

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


class ScraperTemplate:
    """
    Base template for web scrapers.

    This template provides common functionality for web scraping tasks including:
    - Selenium WebDriver setup with SeleniumBase
    - Logging infrastructure
    - Date range handling
    - YAML-based selector configuration
    - Error handling and retries
    - Data collection and storage
    """

    def __init__(self,
                 start_date: str,
                 end_date: str,
                 output_path: str,
                 logs_path: Path,
                 headless: bool = True):
        """
        Initialize the scraper.

        Args:
            start_date: Start date in MM-DD-YYYY format
            end_date: End date in MM-DD-YYYY format
            output_path: Directory path for output files
            logs_path: Directory path for log files
            headless: Whether to run browser in headless mode
        """
        # Initialize logger
        self.logger: Logging = Logging(
            script_name='scraper_name',  # TODO: Update with actual scraper name
            log_dir=logs_path
        )
        self.logger.log(
            message=f"Initializing scraper with start_date={start_date}, "
                    f"end_date={end_date}, output_path={output_path}, "
                    f"headless={headless}",
            category='INFO'
        )

        # Core configuration
        self.url: str = ''  # TODO: Set target URL
        self.start_date: str = datetime.strptime(start_date, '%m-%d-%Y').strftime('%m/%d/%y')
        self.end_date: str = datetime.strptime(end_date, '%m-%d-%Y').strftime('%m/%d/%y')
        self.output_path: str = output_path
        self.headless: bool = headless

        # Load selectors from YAML configuration
        self.selectors: dict[str, str] = self.load_selectors()

        # Data storage
        self.data: list[dict] = []

        # Initialize driver
        self.driver = None

    def get_driver(self) -> Driver:
        """
        Initialize and configure the SeleniumBase WebDriver.

        Returns:
            Configured Driver instance

        Raises:
            SystemExit: If driver setup or initial page load fails
        """
        try:
            self.logger.log(
                message="Setting up SeleniumBase Driver",
                category='INFO'
            )

            # Create persistent profile directory
            profile_path = os.path.join(os.getcwd(), 'chrome_profile')
            os.makedirs(profile_path, exist_ok=True)

            driver = Driver(
                browser='chrome',
                uc=True,  # Enable undetected-chromedriver
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

        # Attempt to fetch website with retries
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
        """
        Main execution method for the scraper.

        Override this method with the specific scraping workflow.
        """
        try:
            self.driver = self.get_driver()

            # TODO: Implement scraping workflow
            # Example workflow:
            # 1. self.search_results()
            # 2. self.collect_results()
            # 3. self.process_data()
            # 4. self.save_data()

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
        """
        Wait for an element to meet a specific condition.

        Args:
            locator: Tuple of (By.TYPE, selector_string)
            timeout: Maximum wait time in seconds
            condition: Expected condition to wait for

        Returns:
            WebElement once condition is met

        Raises:
            TimeoutException: If element doesn't meet condition within timeout
        """
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
        """
        Safely find and extract text from an element.

        Args:
            locator: Tuple of (By.TYPE, selector_string)
            default: Default value if element not found

        Returns:
            Element text or default value
        """
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
        """
        Save collected data to CSV file.

        Args:
            filename: Output filename (without path)
        """
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

    @staticmethod
    def load_selectors() -> dict[str, str]:
        """
        Load CSS/XPath selectors from YAML configuration file.

        Returns:
            Dictionary of selector mappings

        Note:
            Expects selectors.yaml in config/ directory with structure:
            scraper_name:
              selector_name: "selector_value"
        """
        with open('selectors.yaml', 'r') as file:
            config = yaml.safe_load(file)
            # TODO: Update with actual scraper name key
            return config.get('scraper_name', {})


# Example usage
if __name__ == "__main__":
    # Example instantiation
    scraper = ScraperTemplate(
        start_date="01-01-2024",
        end_date="01-31-2024",
        output_path="./output",
        logs_path=Path("./logs"),
        headless=True
    )

    # Run the scraper
    scraper.run()