// Mirrors autohub/api/schemas.py (the OpenAPI document at /openapi.json is authoritative).

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface BrandRef {
  id: number;
  name: string;
  slug: string;
}

export interface Brand extends BrandRef {
  country: string | null;
  description: string | null;
  website_url: string | null;
  logo_url: string | null;
  model_count?: number;
}

export interface BrandDetail extends Brand {
  models: ModelSummary[];
}

export interface VehicleImage {
  id: number;
  model_id: number;
  generation_id: number | null;
  variant_id: number | null;
  category: string;
  image_url: string;
  thumbnail_url: string | null;
  source_page_url: string | null;
  source_domain: string | null;
  source_kind: string;
  is_official: boolean;
  title: string | null;
  width: number | null;
  height: number | null;
  validation_status: string;
  is_primary: boolean;
}

export interface KeySpecRange {
  key: string;
  name: string;
  unit: string | null;
  min: number;
  max: number;
  display: string;
}

export interface ModelSummary {
  id: number;
  name: string;
  slug: string;
  brand: BrandRef;
  body_type: string | null;
  segment: string | null;
  description: string | null;
  variant_count: number;
  fuel_types: string[];
  transmission_types: string[];
  price_min: number | null;
  price_max: number | null;
  primary_image: VehicleImage | null;
  key_specs: KeySpecRange[];
}

export interface Variant {
  id: number;
  generation_id: number;
  name: string;
  slug: string;
  fuel_type: string | null;
  transmission_type: string | null;
  drivetrain: string | null;
  price_ex_showroom: number | null;
  price_currency: string | null;
  price_source: string | null;
  price_as_of: string | null;
}

export interface VariantDetail extends Variant {
  model_id: number;
  model_name: string;
  model_slug: string;
  brand: BrandRef;
  generation_name: string;
}

export interface Generation {
  id: number;
  model_id: number;
  name: string;
  code: string | null;
  year_start: number | null;
  year_end: number | null;
  is_current: boolean;
  variants: Variant[];
}

export interface ModelDetail extends ModelSummary {
  generations: Generation[];
  image_counts: Record<string, number>;
  brochure_count: number;
  data_sources: string[];
}

export interface SpecSource {
  type: string;
  brochure_id?: number | null;
  brochure_version_id?: number | null;
  brochure_title?: string | null;
  page?: number | null;
  url?: string | null;
}

export interface SpecValue {
  key: string;
  name: string;
  data_type: string;
  unit: string | null;
  value_num: number | null;
  value_bool: boolean | null;
  value_text: string | null;
  display: string;
  status: string;
  original_value: string | null;
  source: SpecSource;
  confidence: number | null;
  flags: string[];
}

export interface SpecGroup {
  key: string;
  name: string;
  specs: SpecValue[];
}

export interface VariantSpecs {
  variant_id: number;
  groups: SpecGroup[];
  total: number;
}

export interface SpecDefinition {
  key: string;
  name: string;
  data_type: string;
  canonical_unit: string | null;
  is_key_spec: boolean;
}

export interface SpecCategory {
  key: string;
  name: string;
  definitions: SpecDefinition[];
}

export interface CompareCell {
  variant_id: number;
  status: string;
  display: string | null;
  source_type: string | null;
  is_best?: boolean;
}

export interface CompareRow {
  key: string;
  name: string;
  data_type: string;
  unit: string | null;
  differs: boolean;
  best_variant_ids: number[];
  cells: CompareCell[];
}

export interface CompareOut {
  variants: VariantDetail[];
  groups: { key: string; name: string; rows: CompareRow[] }[];
  rows_total: number;
  rows_differing: number;
}

export interface News {
  id: number;
  title: string;
  slug: string | null;
  summary: string | null;
  content: string;
  source_name: string | null;
  source_url: string | null;
  published_at: string;
  news_images: { image_url: string }[];
  vehicles: { id: number; name: string; slug: string; brand: BrandRef }[];
}

export interface User {
  id: number;
  name: string;
  email: string;
  gender: string | null;
  location: string | null;
  role: string;
  is_active: boolean;
  created_at: string;
}

export interface Token {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export type SegmentKind = "verified_fact" | "sourced_info" | "explanation";

export interface AnswerSegment {
  kind: SegmentKind;
  text: string;
  evidence_ids: string[];
}

export interface Evidence {
  id: string;
  kind: "brochure_fact" | "sourced" | "catalogue" | "note";
  text: string;
  source: SpecSource & Record<string, unknown>;
  refs: Record<string, unknown>;
  confidence: number | null;
}

export interface Conversation {
  id: string;
  title: string | null;
  context_model_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface ApiErrorBody {
  error: { code: string; message: string; details?: unknown; request_id?: string };
}
