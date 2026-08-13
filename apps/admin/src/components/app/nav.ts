import { LayoutDashboard, Users, Shield, type LucideIcon } from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Permission required to see this item (undefined = always visible). */
  permission?: string;
}

export const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/users", label: "Users", icon: Users, permission: "user:read" },
  { href: "/roles", label: "Roles", icon: Shield, permission: "role:read" },
];
