"""The vehicle specification taxonomy — single source of truth.

Everything that needs to know "which specifications exist" derives from this
module: the database seed, the LLM extraction schema and prompt, search
validation and (through the API) the frontend.

To add a specification, add one ``SpecDef`` line and run the taxonomy seed
(``alembic upgrade head`` or ``python -m tools.seed_taxonomy``). No schema
change is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DataType = Literal["number", "text", "boolean"]

EV = ("electric",)
PLUG = ("electric", "phev")
ELECTRIFIED = ("electric", "phev", "hybrid")
COMBUSTION = ("petrol", "diesel", "cng", "hybrid", "phev", "lpg", "flex_fuel")


@dataclass(frozen=True)
class SpecDef:
    key: str
    name: str
    data_type: DataType
    unit: str | None = None
    higher_is_better: bool | None = None
    key_spec: bool = False
    filterable: bool = True
    applies_to_fuel: tuple[str, ...] | None = None
    description: str | None = None


@dataclass(frozen=True)
class SpecCategoryDef:
    key: str
    name: str
    specs: tuple[SpecDef, ...]


def _n(key: str, name: str, unit: str | None = None, **kw: object) -> SpecDef:
    return SpecDef(key, name, "number", unit, **kw)  # type: ignore[arg-type]


def _t(key: str, name: str, **kw: object) -> SpecDef:
    return SpecDef(key, name, "text", None, filterable=False, **kw)  # type: ignore[arg-type]


def _b(key: str, name: str, **kw: object) -> SpecDef:
    return SpecDef(key, name, "boolean", None, **kw)  # type: ignore[arg-type]


TAXONOMY: tuple[SpecCategoryDef, ...] = (
    SpecCategoryDef(
        "dimensions",
        "Dimensions & Capacity",
        (
            _n("length", "Length", "mm", key_spec=True),
            _n("width", "Width", "mm"),
            _n("height", "Height", "mm"),
            _n("wheelbase", "Wheelbase", "mm", higher_is_better=True),
            _n("ground_clearance", "Ground clearance", "mm", higher_is_better=True, key_spec=True),
            _n("boot_capacity", "Boot capacity", "l", higher_is_better=True, key_spec=True),
            _n(
                "fuel_tank_capacity",
                "Fuel tank capacity",
                "l",
                higher_is_better=True,
                applies_to_fuel=COMBUSTION,
            ),
            _n("seating_capacity", "Seating capacity", None, key_spec=True),
            _n("kerb_weight", "Kerb weight", "kg"),
            _n("gross_vehicle_weight", "Gross vehicle weight", "kg"),
            _n("turning_radius", "Turning radius", "m", higher_is_better=False),
            _n("water_wading_depth", "Water wading depth", "mm", higher_is_better=True),
            _n("approach_angle", "Approach angle", "deg", higher_is_better=True),
            _n("departure_angle", "Departure angle", "deg", higher_is_better=True),
        ),
    ),
    SpecCategoryDef(
        "engine",
        "Engine & Performance",
        (
            _t("engine_name", "Engine", applies_to_fuel=COMBUSTION),
            _n("engine_displacement", "Displacement", "cc", key_spec=True, applies_to_fuel=COMBUSTION),
            _t("engine_configuration", "Engine configuration", applies_to_fuel=COMBUSTION),
            _n("cylinders", "Cylinders", None, applies_to_fuel=COMBUSTION),
            _n("max_power", "Maximum power", "kW", higher_is_better=True, key_spec=True),
            _n("max_power_rpm", "Maximum power at", "rpm", filterable=False),
            _n("max_torque", "Maximum torque", "Nm", higher_is_better=True, key_spec=True),
            _n("max_torque_rpm", "Maximum torque at", "rpm", filterable=False),
            _b("turbocharged", "Turbocharged", applies_to_fuel=COMBUSTION),
            _t("fuel_injection", "Fuel injection", applies_to_fuel=COMBUSTION),
            _t("emission_standard", "Emission standard", applies_to_fuel=COMBUSTION),
            _n("top_speed", "Top speed", "km/h", higher_is_better=True),
            _n("acceleration_0_100", "0-100 km/h", "s", higher_is_better=False),
            _t("drive_modes", "Drive modes"),
        ),
    ),
    SpecCategoryDef(
        "transmission",
        "Transmission & Drivetrain",
        (
            _t("transmission_description", "Transmission"),
            _n("gears", "Number of gears", None),
            _t("drive_type_description", "Drive type"),
            _t("differential", "Differential"),
            _b("four_wheel_drive_low_range", "Low-range transfer case"),
        ),
    ),
    SpecCategoryDef(
        "fuel_efficiency",
        "Fuel, Battery & Efficiency",
        (
            _n(
                "mileage_arai",
                "Certified mileage",
                "km/l",
                higher_is_better=True,
                key_spec=True,
                applies_to_fuel=COMBUSTION,
                description="Manufacturer-claimed / ARAI-certified figure. km/kg for CNG is stored in original_unit.",
            ),
            _n(
                "mileage_real_world",
                "Real-world mileage",
                "km/l",
                higher_is_better=True,
                applies_to_fuel=COMBUSTION,
                description="Only from an attributed third-party source; never taken from a brochure.",
            ),
            _n(
                "battery_capacity",
                "Battery capacity",
                "kWh",
                higher_is_better=True,
                key_spec=True,
                applies_to_fuel=ELECTRIFIED,
            ),
            _t("battery_type", "Battery type", applies_to_fuel=ELECTRIFIED),
            _n(
                "ev_range",
                "Certified range",
                "km",
                higher_is_better=True,
                key_spec=True,
                applies_to_fuel=PLUG,
            ),
            _t("range_test_cycle", "Range test cycle", applies_to_fuel=PLUG),
            _n("motor_power", "Motor power", "kW", higher_is_better=True, applies_to_fuel=ELECTRIFIED),
            _n("motor_torque", "Motor torque", "Nm", higher_is_better=True, applies_to_fuel=ELECTRIFIED),
            _t("charging_time_ac", "AC charging time", applies_to_fuel=PLUG),
            _t("charging_time_dc", "DC fast charging time", applies_to_fuel=PLUG),
            _n(
                "dc_fast_charging_power",
                "DC fast charging power",
                "kW",
                higher_is_better=True,
                applies_to_fuel=PLUG,
            ),
            _t("charging_standard", "Charging standard", applies_to_fuel=PLUG),
        ),
    ),
    SpecCategoryDef(
        "suspension_brakes",
        "Suspension, Brakes & Wheels",
        (
            _t("front_suspension", "Front suspension"),
            _t("rear_suspension", "Rear suspension"),
            _t("front_brakes", "Front brakes"),
            _t("rear_brakes", "Rear brakes"),
            _t("steering_type", "Steering"),
            _t("tyre_size", "Tyre size"),
            _n("wheel_size", "Wheel size", "inch"),
            _t("spare_wheel", "Spare wheel"),
        ),
    ),
    SpecCategoryDef(
        "safety",
        "Safety",
        (
            _n("airbags", "Airbags", None, higher_is_better=True, key_spec=True),
            _b("abs", "ABS"),
            _b("ebd", "EBD"),
            _b("esc", "Electronic stability control"),
            _b("traction_control", "Traction control"),
            _b("hill_hold_assist", "Hill-hold assist"),
            _b("hill_descent_control", "Hill-descent control"),
            _b("isofix", "ISOFIX child-seat mounts"),
            _b("adas", "ADAS"),
            _t("adas_features", "ADAS features"),
            _n("ncap_rating", "NCAP safety rating", "stars", higher_is_better=True),
            _t("ncap_body", "NCAP programme"),
            _b("tpms", "Tyre pressure monitoring"),
            _b("front_parking_sensors", "Front parking sensors"),
            _b("rear_parking_sensors", "Rear parking sensors"),
            _b("rear_camera", "Rear-view camera"),
            _b("camera_360", "360-degree camera"),
            _b("blind_spot_monitor", "Blind-spot monitor"),
            _b("electronic_parking_brake", "Electronic parking brake"),
            _b("all_wheel_disc_brakes", "All-wheel disc brakes"),
        ),
    ),
    SpecCategoryDef(
        "comfort",
        "Interior & Comfort",
        (
            _t("seat_material", "Seat upholstery"),
            _t("driver_seat_adjustment", "Driver seat adjustment"),
            _b("powered_driver_seat", "Powered driver seat"),
            _b("ventilated_seats", "Ventilated seats"),
            _b("heated_seats", "Heated seats"),
            _t("climate_control", "Climate control"),
            _b("automatic_climate_control", "Automatic climate control"),
            _b("sunroof", "Sunroof"),
            _t("sunroof_type", "Sunroof type"),
            _b("ambient_lighting", "Ambient lighting"),
            _b("rear_ac_vents", "Rear AC vents"),
            _b("wireless_charging", "Wireless phone charging"),
            _b("cruise_control", "Cruise control"),
            _b("adaptive_cruise_control", "Adaptive cruise control"),
            _b("keyless_entry", "Keyless entry"),
            _b("push_button_start", "Push-button start"),
            _b("auto_dimming_irvm", "Auto-dimming inside mirror"),
            _b("powered_tailgate", "Powered tailgate"),
            _b("air_purifier", "Air purifier"),
        ),
    ),
    SpecCategoryDef(
        "infotainment",
        "Infotainment & Technology",
        (
            _n("touchscreen_size", "Touchscreen size", "inch", higher_is_better=True),
            _n("instrument_cluster_size", "Digital instrument cluster size", "inch", higher_is_better=True),
            _t("audio_system", "Audio system"),
            _n("speakers", "Speakers", None, higher_is_better=True),
            _b("android_auto", "Android Auto"),
            _b("apple_carplay", "Apple CarPlay"),
            _b("wireless_smartphone_integration", "Wireless Android Auto / CarPlay"),
            _b("connected_car", "Connected car technology"),
            _b("head_up_display", "Head-up display"),
            _b("ota_updates", "Over-the-air updates"),
            _b("voice_assistant", "Voice assistant"),
        ),
    ),
    SpecCategoryDef(
        "exterior",
        "Exterior",
        (
            _t("headlamp_type", "Headlamps"),
            _b("led_headlamps", "LED headlamps"),
            _b("drl", "Daytime running lamps"),
            _t("tail_lamp_type", "Tail lamps"),
            _b("fog_lamps", "Fog lamps"),
            _b("alloy_wheels", "Alloy wheels"),
            _b("roof_rails", "Roof rails"),
            _b("rain_sensing_wipers", "Rain-sensing wipers"),
            _b("auto_headlamps", "Automatic headlamps"),
            _b("powered_orvm", "Power-adjustable outside mirrors"),
            _t("exterior_highlights", "Exterior styling highlights"),
        ),
    ),
)


ALL_SPECS: dict[str, SpecDef] = {spec.key: spec for category in TAXONOMY for spec in category.specs}
CATEGORY_OF: dict[str, str] = {spec.key: category.key for category in TAXONOMY for spec in category.specs}
CATEGORY_NAMES: dict[str, str] = {category.key: category.name for category in TAXONOMY}

# Specs summarised on cards and in the "model" search document.
KEY_SPEC_KEYS: tuple[str, ...] = tuple(key for key, spec in ALL_SPECS.items() if spec.key_spec)


def get_spec(key: str) -> SpecDef | None:
    return ALL_SPECS.get(key)


def validate_taxonomy() -> None:
    """Fail fast on a malformed taxonomy (run at import and in tests)."""
    seen: set[str] = set()
    for category in TAXONOMY:
        for spec in category.specs:
            if spec.key in seen:
                raise ValueError(f"Duplicate spec key: {spec.key}")
            seen.add(spec.key)
            if spec.data_type != "number" and spec.unit is not None:
                raise ValueError(f"Non-numeric spec '{spec.key}' must not declare a unit")
            if spec.higher_is_better is not None and spec.data_type != "number":
                raise ValueError(f"'higher_is_better' only applies to numeric specs: {spec.key}")
    if len({c.key for c in TAXONOMY}) != len(TAXONOMY):
        raise ValueError("Duplicate category key")


validate_taxonomy()
