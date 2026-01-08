"""
Run AutoHub brochure automation pipeline.
"""
from autohub.automation.discovery.mahindra import (
    discover_mahindra_brochures,
    save_discovery,
    )

from autohub.automation.brochures.downloader.brochure_downloader import run_brochure_downloader

def run_brochure_pipeline():

    print("Starting Mahindra brochure discovery...")
    data = discover_mahindra_brochures()
    save_discovery(data)
    print("Discovery completed.")

    print("Starting brochure downloader...")
    run_brochure_downloader()
    print("Brochure download completed.")

if __name__ == "__main__":
    run_brochure_pipeline()
