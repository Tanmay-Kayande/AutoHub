const LAKH = 100_000;
const CRORE = 10_000_000;

/** ₹ 12.49 lakh / ₹ 1.05 crore, the way Indian buyers read prices. */
export function formatPrice(value: number | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  if (value >= CRORE) return `₹${(value / CRORE).toFixed(2)} crore`;
  return `₹${(value / LAKH).toFixed(2)} lakh`;
}

export function priceRange(min: number | null, max: number | null): string | null {
  const low = formatPrice(min);
  const high = formatPrice(max);
  if (!low) return null;
  return high && high !== low ? `${low} – ${high}` : low;
}

const CODE_LABELS: Record<string, string> = {
  suv: "SUV",
  mpv: "MPV",
  amt: "AMT",
  cvt: "CVT",
  dct: "DCT",
  cng: "CNG",
  lpg: "LPG",
  phev: "Plug-in hybrid",
  fwd: "FWD",
  rwd: "RWD",
  awd: "AWD",
  "4wd": "4WD",
  single_speed: "Single-speed",
  flex_fuel: "Flex fuel",
};

export function label(code: string | null | undefined): string {
  if (!code) return "";
  return CODE_LABELS[code] ?? code.charAt(0).toUpperCase() + code.slice(1).replace(/_/g, " ");
}

export function sourceLabel(type: string | null | undefined): string {
  switch (type) {
    case "official_brochure":
      return "Official brochure";
    case "official_website":
      return "Official website";
    case "legacy_v1":
      return "Unverified (legacy)";
    case "third_party":
      return "Third-party source";
    case "manual":
      return "Edited by AutoHub";
    default:
      return "Source unknown";
  }
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

export function modelHref(brandSlug: string, modelSlug: string): string {
  return `/cars/${brandSlug}/${modelSlug}`;
}
