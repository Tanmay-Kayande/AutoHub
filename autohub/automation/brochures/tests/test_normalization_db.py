from autohub.automation.normalizer.car_normalizer import normalize_variant
from autohub.automation.db_writer.car_writer import write_car_payload
from autohub.database.connection import session_local

raw_result = {
    "car_brand": "Mahindra",
    "car_model": "Thar Roxx",
    "variants": [
        {
            "variant_name": "MX5 Diesel 4x4 AT",
            "engine": "mHawk",
            "engine_capacity": "2.2 L",
            "fuel_type": "Diesel",
            "transmission": "6-Speed Torque Converter Automatic",
            "power": "128.6 kW @ 3500 rpm",
            "torque": "400 Nm @ 1750-2750 rpm",
            "mileage": "15 kmpl",
            "price": "1890000"
        }
    ]
}

def run_test():
    db = session_local()

    try:
        for variant in raw_result["variants"]:
            normalized = normalize_variant(
                variant,
                raw_result["car_brand"],
                raw_result["car_model"]
            )

            print("\n--- NORMALIZED OUTPUT ---")
            print(normalized)

            write_car_payload(normalized, db)

        db.commit()
        print("\nDB Write Successful")

    except Exception as e:
        db.rollback()
        print("\nTest Failed:", e)

    finally:
        db.close()

if __name__ == "__main__":
    run_test()        