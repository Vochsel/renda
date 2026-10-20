"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const ITEMS = [
  { href: "/dashboard", label: "Overview" },
  { href: "/dashboard/playground", label: "Playground" },
  { href: "/dashboard/jobs", label: "Jobs" },
  { href: "/dashboard/gallery", label: "Gallery" },
  { href: "/dashboard/keys", label: "API keys" },
  { href: "/dashboard/billing", label: "Billing" },
  { href: "/docs", label: "Docs" },
];

export function DashboardNav() {
  const path = usePathname();
  return (
    <nav className="flex gap-1 overflow-x-auto md:flex-col">
      {ITEMS.map((i) => {
        const active = i.href === "/dashboard" ? path === i.href : path.startsWith(i.href);
        return (
          <Link key={i.href} href={i.href}
            className={cn("rounded-md px-3 py-1.5 text-sm whitespace-nowrap", active ? "bg-muted text-foreground font-medium" : "text-muted-foreground hover:text-foreground")}>
            {i.label}
          </Link>
        );
      })}
    </nav>
  );
}
