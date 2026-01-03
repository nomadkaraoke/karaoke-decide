"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { AdminPage } from "@/components/AdminPage";
import {
  BarChartIcon,
  UsersIcon,
  ActivityIcon,
  ChevronLeftIcon,
} from "@/components/icons";

const adminNavLinks = [
  { href: "/admin", label: "Dashboard", icon: BarChartIcon },
  { href: "/admin/users", label: "Users", icon: UsersIcon },
  { href: "/admin/sync-jobs", label: "Sync Jobs", icon: ActivityIcon },
];

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();

  return (
    <AdminPage>
      <div className="min-h-screen bg-[#0a0a0f]">
        {/* Admin Header */}
        <div className="border-b border-white/10 bg-[#0a0a0f]/80 backdrop-blur-xl">
          <div className="max-w-7xl mx-auto px-4 py-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-4">
                <Link
                  href="/"
                  className="flex items-center gap-2 text-white/60 hover:text-white transition-colors"
                >
                  <ChevronLeftIcon className="w-4 h-4" />
                  <span className="text-sm">Back to App</span>
                </Link>
                <div className="h-6 w-px bg-white/10" />
                <h1 className="text-lg font-semibold text-white">Admin</h1>
              </div>
            </div>
          </div>
        </div>

        <div className="max-w-7xl mx-auto px-4 py-6">
          <div className="flex gap-6">
            {/* Sidebar */}
            <nav className="w-48 shrink-0">
              <div className="sticky top-24 space-y-1">
                {adminNavLinks.map((link) => {
                  const isActive =
                    pathname === link.href ||
                    (link.href !== "/admin" && pathname.startsWith(link.href));
                  const Icon = link.icon;
                  return (
                    <Link
                      key={link.href}
                      href={link.href}
                      className={`
                        flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors
                        ${
                          isActive
                            ? "bg-white/10 text-white"
                            : "text-white/60 hover:text-white hover:bg-white/5"
                        }
                      `}
                    >
                      <Icon className="w-4 h-4" />
                      {link.label}
                    </Link>
                  );
                })}
              </div>
            </nav>

            {/* Main Content */}
            <main className="flex-1 min-w-0">{children}</main>
          </div>
        </div>
      </div>
    </AdminPage>
  );
}
