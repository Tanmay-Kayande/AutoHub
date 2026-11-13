// Typed client for the AutoHub API. Works in server components (no token) and in the browser.
import type {
  Brand,
  BrandDetail,
  CompareOut,
  Conversation,
  ModelDetail,
  ModelSummary,
  News,
  Page,
  SpecCategory,
  Token,
  User,
  VariantDetail,
  VariantSpecs,
  VehicleImage,
} from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8001").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

type Query = Record<string, string | number | undefined | null>;

function url(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const qs = params.toString();
  return `${API_URL}/api/v1${path}${qs ? `?${qs}` : ""}`;
}

export async function request<T>(
  path: string,
  init: RequestInit & { query?: Query; token?: string | null; revalidate?: number } = {},
): Promise<T> {
  const { query, token, revalidate, headers, ...rest } = init;
  let response: Response;
  try {
    response = await fetch(url(path, query), {
      ...rest,
      headers: { Accept: "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...headers },
      ...(revalidate !== undefined ? { next: { revalidate } } : { cache: "no-store" }),
    });
  } catch {
    throw new ApiError(0, "network_error", "AutoHub's server could not be reached. Check that the API is running.");
  }
  if (!response.ok) {
    let code = "http_error";
    let message = `Request failed (${response.status}).`;
    let details: unknown;
    try {
      const body = await response.json();
      code = body?.error?.code ?? code;
      message = body?.error?.message ?? message;
      details = body?.error?.details;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, code, message, details);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const CATALOGUE_TTL = 60;

export const api = {
  brands: (q?: string) => request<Page<Brand>>("/brands", { query: { q, page_size: 100 }, revalidate: CATALOGUE_TTL }),
  brand: (slug: string) => request<BrandDetail>(`/brands/${encodeURIComponent(slug)}`, { revalidate: CATALOGUE_TTL }),
  models: (query: Query = {}) =>
    request<Page<ModelSummary>>("/models", { query: { page_size: 50, ...query }, revalidate: CATALOGUE_TTL }),
  model: (brand: string, model: string) =>
    request<ModelDetail>(`/models/${encodeURIComponent(brand)}/${encodeURIComponent(model)}`, {
      revalidate: CATALOGUE_TTL,
    }),
  images: (modelId: number) => request<VehicleImage[]>(`/models/${modelId}/images`, { revalidate: CATALOGUE_TTL }),
  variants: (query: Query) =>
    request<Page<VariantDetail>>("/variants", { query: { page_size: 100, ...query }, revalidate: CATALOGUE_TTL }),
  variantSpecs: (id: number) => request<VariantSpecs>(`/variants/${id}/specs`, { revalidate: CATALOGUE_TTL }),
  specDefinitions: () => request<SpecCategory[]>("/specs/definitions", { revalidate: 3600 }),
  compare: (ids: number[]) => request<CompareOut>("/compare", { query: { variant_ids: ids.join(",") } }),
  news: (page = 1) => request<Page<News>>("/news", { query: { page, page_size: 12 }, revalidate: CATALOGUE_TTL }),
  newsItem: (id: number) => request<News>(`/news/${id}`, { revalidate: CATALOGUE_TTL }),
  suggestions: (modelId?: number) => request<string[]>("/assistant/suggestions", { query: { model_id: modelId } }),

  login: (email: string, password: string) =>
    request<Token>("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ username: email, password }).toString(),
    }),
  signup: (data: { name: string; email: string; password: string; location?: string }) =>
    request<User>("/auth/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  me: (token: string) => request<User>("/auth/me", { token }),
  conversations: (token: string) => request<Conversation[]>("/assistant/conversations", { token }),
};

export function streamUrl(): string {
  return url("/assistant/ask/stream");
}
