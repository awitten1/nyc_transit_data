import os
import re
import requests
from bs4 import BeautifulSoup

# Target URL for the TLC trip record data
TLC_URL = "https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page"

# Directory where the files will be saved
SAVE_DIR = "yellow_taxi_records"


def download_yellow_taxi_data():
    # Create the target directory if it doesn't exist
    if not os.path.exists(SAVE_DIR):
        os.makedirs(SAVE_DIR)
        print(f"Created directory: {SAVE_DIR}")

    print(f"Fetching page content from {TLC_URL}...")
    try:
        response = requests.get(TLC_URL)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Failed to load the TLC page: {e}")
        return

    soup = BeautifulSoup(response.text, "html.parser")

    # Regex pattern to identify Yellow Taxi parquet links and capture the Year (group 1) and Month (group 2)
    # Example match: https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2023-01.parquet
    url_pattern = re.compile(r"yellow_tripdata_(\d{4})-(\d{2})\.parquet", re.IGNORECASE)

    # Find all anchor tags on the page
    links = soup.find_all("a")
    downloaded_count = 0
    for link in links:
        href = link.get("href")
        if not href:
            continue

        # Clean the URL of any accidental spaces on the TLC website
        href = href.strip()

        match = url_pattern.search(href)
        if match:
            year = match.group(1)
            month = match.group(2)

            # Format the filename as requested
            filename = f"yellow_taxi_{year}_{month}.parquet"
            filepath = os.path.join(SAVE_DIR, filename)

            # Skip downloading if the file already exists locally
            if os.path.exists(filepath):
                print(f"File {filename} already exists. Skipping...")
                continue

            print(f"Downloading data for {year}-{month}...")

            try:
                # Stream the download to handle large file sizes efficiently
                file_response = requests.get(href, stream=True)
                file_response.raise_for_status()

                with open(filepath, "wb") as f:
                    for chunk in file_response.iter_content(chunk_size=8192):
                        f.write(chunk)

                print(f"Successfully saved: {filename}")
                downloaded_count += 1

            except requests.RequestException as e:
                print(f"Failed to download {href}: {e}")

    print(f"\nFinished! Downloaded {downloaded_count} new files into '{SAVE_DIR}'.")


if __name__ == "__main__":
    download_yellow_taxi_data()
