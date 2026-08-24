// Catalogue resource specs (Stage 2.9).
//
// The four catalogue screens are the same screen with different fields, so the
// shape is declared here once and rendered by <CatalogueResource>. Adding a
// catalogue entity is a spec entry plus a three-line page, mirroring the
// backend's scripts/scaffold_module.py.

export type FieldType = "text" | "number" | "decimal" | "boolean" | "select" | "lookup";

export interface FieldSpec {
  /** Request/response key on the API DTO. */
  name: string;
  label: string;
  type: FieldType;
  /** Rendered in the create form (all fields are shown in the table). */
  required?: boolean;
  /** Options for `select`. */
  options?: string[];
  /** For `lookup`: the API collection to load ids/names from. */
  lookup?: string;
  /** Show this column in the list table (default true). */
  inTable?: boolean;
  /** Show this field in the create form (default true). */
  inForm?: boolean;
  default?: string | number | boolean;
  placeholder?: string;
}

export interface CatalogueSpec {
  /** API collection path, also the react-query key. */
  path: string;
  title: string;
  subtitle: string;
  /** Singular noun for buttons ("New vehicle"). */
  singular: string;
  readPermission: string;
  managePermission: string;
  fields: FieldSpec[];
}

/** A row from any catalogue collection: id + slug/name + the spec's own fields. */
export interface CatalogueRow {
  id: string;
  name: string;
  slug?: string;
  [key: string]: unknown;
}

export const DESTINATIONS: CatalogueSpec = {
  path: "destinations",
  title: "Destinations",
  subtitle: "Parks, reserves, towns and other places an itinerary can visit.",
  singular: "destination",
  readPermission: "destination:read",
  managePermission: "destination:manage",
  fields: [
    { name: "name", label: "Name", type: "text", required: true },
    {
      name: "type",
      label: "Type",
      type: "select",
      options: ["park", "reserve", "conservancy", "city", "town", "beach", "other"],
      default: "park",
    },
    { name: "country", label: "Country", type: "text", default: "Kenya" },
    { name: "region", label: "Region", type: "text" },
    { name: "is_active", label: "Active", type: "boolean", default: true },
  ],
};

export const ACCOMMODATIONS: CatalogueSpec = {
  path: "accommodations",
  title: "Accommodations",
  subtitle: "Lodges, camps and hotels. Room types and seasonal rates hang off each one.",
  singular: "accommodation",
  readPermission: "accommodation:read",
  managePermission: "accommodation:manage",
  fields: [
    { name: "name", label: "Name", type: "text", required: true },
    {
      name: "destination_id",
      label: "Destination",
      type: "lookup",
      lookup: "destinations",
      required: true,
    },
    {
      name: "category",
      label: "Category",
      type: "select",
      options: ["lodge", "tented_camp", "hotel", "guest_house", "villa", "other"],
      default: "lodge",
    },
    { name: "star_rating", label: "Stars", type: "number" },
    { name: "is_active", label: "Active", type: "boolean", default: true },
  ],
};

export const ACTIVITIES: CatalogueSpec = {
  path: "activities",
  title: "Activities",
  subtitle: "Game drives, balloon safaris, cultural visits and other priced experiences.",
  singular: "activity",
  readPermission: "activity:read",
  managePermission: "activity:manage",
  fields: [
    { name: "name", label: "Name", type: "text", required: true },
    { name: "destination_id", label: "Destination", type: "lookup", lookup: "destinations" },
    { name: "duration_minutes", label: "Duration (min)", type: "number" },
    { name: "is_optional", label: "Optional", type: "boolean", default: true },
    { name: "is_active", label: "Active", type: "boolean", default: true },
  ],
};

export const VEHICLES: CatalogueSpec = {
  path: "vehicles",
  title: "Vehicles",
  subtitle: "Fleet used for transport costing. Fuel economy drives the fuel line on a quote.",
  singular: "vehicle",
  readPermission: "vehicle:read",
  managePermission: "vehicle:manage",
  fields: [
    { name: "name", label: "Name", type: "text", required: true },
    {
      name: "vehicle_type",
      label: "Type",
      type: "select",
      options: ["safari_land_cruiser", "safari_van", "minibus", "saloon", "suv", "other"],
      default: "safari_land_cruiser",
    },
    { name: "registration", label: "Reg.", type: "text" },
    { name: "passenger_capacity", label: "Seats", type: "number", default: 6 },
    { name: "fuel_type", label: "Fuel", type: "text", default: "diesel" },
    {
      name: "fuel_consumption_kmpl",
      label: "km/L",
      type: "decimal",
      required: true,
      placeholder: "7",
    },
    { name: "driver_cost_per_day", label: "Driver/day", type: "decimal", default: "0" },
    { name: "daily_operating_cost", label: "Operating/day", type: "decimal", default: "0" },
    { name: "currency", label: "Currency", type: "text", required: true, default: "USD" },
    { name: "is_active", label: "Active", type: "boolean", default: true },
  ],
};
