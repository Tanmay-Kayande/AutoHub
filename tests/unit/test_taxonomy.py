from autohub.db.models import FUEL_TYPES
from autohub.specs.taxonomy import ALL_SPECS, CATEGORY_OF, KEY_SPEC_KEYS, TAXONOMY, validate_taxonomy
from autohub.specs.units import UNIT_FAMILIES


def test_taxonomy_is_well_formed() -> None:
    validate_taxonomy()
    assert len(ALL_SPECS) == sum(len(c.specs) for c in TAXONOMY)
    assert set(CATEGORY_OF) == set(ALL_SPECS)


def test_all_nine_categories_from_the_brief_exist() -> None:
    assert [c.key for c in TAXONOMY] == [
        "dimensions",
        "engine",
        "transmission",
        "fuel_efficiency",
        "suspension_brakes",
        "safety",
        "comfort",
        "infotainment",
        "exterior",
    ]


def test_every_numeric_unit_has_a_conversion_family() -> None:
    missing = {
        spec.key: spec.unit
        for spec in ALL_SPECS.values()
        if spec.data_type == "number" and spec.unit is not None and spec.unit not in UNIT_FAMILIES
    }
    assert missing == {}


def test_fuel_applicability_uses_known_fuel_codes() -> None:
    for spec in ALL_SPECS.values():
        for fuel in spec.applies_to_fuel or ():
            assert fuel in FUEL_TYPES, (spec.key, fuel)


def test_specs_named_in_the_brief_are_present() -> None:
    required = {
        "length", "width", "height", "wheelbase", "ground_clearance", "boot_capacity", "fuel_tank_capacity",
        "seating_capacity", "kerb_weight", "gross_vehicle_weight", "turning_radius",
        "engine_displacement", "engine_configuration", "cylinders", "max_power", "max_torque",
        "max_power_rpm", "max_torque_rpm", "turbocharged", "fuel_injection", "emission_standard",
        "top_speed", "acceleration_0_100",
        "transmission_description", "gears", "drive_type_description", "differential",
        "mileage_arai", "mileage_real_world", "battery_capacity", "ev_range", "charging_time_ac",
        "charging_time_dc", "charging_standard",
        "front_suspension", "rear_suspension", "front_brakes", "rear_brakes", "steering_type",
        "tyre_size", "wheel_size",
        "airbags", "abs", "ebd", "esc", "traction_control", "hill_hold_assist", "adas", "adas_features",
        "ncap_rating", "isofix",
        "seat_material", "driver_seat_adjustment", "ventilated_seats", "climate_control", "sunroof",
        "ambient_lighting", "rear_ac_vents", "wireless_charging",
        "touchscreen_size", "instrument_cluster_size", "audio_system", "speakers", "android_auto",
        "apple_carplay", "connected_car",
        "headlamp_type", "drl", "tail_lamp_type", "alloy_wheels", "roof_rails", "exterior_highlights",
    }  # fmt: skip
    assert required - set(ALL_SPECS) == set()


def test_key_specs_are_numeric() -> None:
    assert KEY_SPEC_KEYS
    assert all(ALL_SPECS[key].data_type == "number" for key in KEY_SPEC_KEYS)
