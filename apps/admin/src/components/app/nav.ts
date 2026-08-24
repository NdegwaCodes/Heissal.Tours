import {
  LayoutDashboard,
  Users,
  Shield,
  MapPin,
  BedDouble,
  Compass,
  Truck,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Permission required to see this item (undefined = always visible). */
  permission?: string;
  /** Optional group heading this item sits under in the sidebar. */
  section?: string;
}

export const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  {
    href: "/catalogue/destinations",
    label: "Destinations",
    icon: MapPin,
    permission: "destination:read",
    section: "Catalogue",
  },
  {
    href: "/catalogue/accommodations",
    label: "Accommodations",
    icon: BedDouble,
    permission: "accommodation:read",
    section: "Catalogue",
  },
  {
    href: "/catalogue/activities",
    label: "Activities",
    icon: Compass,
    permission: "activity:read",
    section: "Catalogue",
  },
  {
    href: "/catalogue/vehicles",
    label: "Vehicles",
    icon: Truck,
    permission: "vehicle:read",
    section: "Catalogue",
  },
  { href: "/users", label: "Users", icon: Users, permission: "user:read", section: "Admin" },
  { href: "/roles", label: "Roles", icon: Shield, permission: "role:read", section: "Admin" },
];
