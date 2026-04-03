"use client";

import { useEffect } from "react";
import { useRouter } from "@/i18n/routing";
import { useTranslations } from "next-intl";

/**
 * Redirect from old /services route to new /my-data page
 */
export default function ServicesRedirect() {
  const router = useRouter();
  const t = useTranslations('services');

  useEffect(() => {
    router.replace("/my-data");
  }, [router]);

  return (
    <div className="min-h-screen flex items-center justify-center">
      <p className="text-[var(--text-muted)]">{t("redirecting")}</p>
    </div>
  );
}
