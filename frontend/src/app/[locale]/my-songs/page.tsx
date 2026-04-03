"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

/**
 * Redirect from old /my-songs route to new /my-data page
 */
export default function MySongsRedirect() {
  const router = useRouter();
  const t = useTranslations("mySongs");

  useEffect(() => {
    router.replace("/my-data");
  }, [router]);

  return (
    <div className="min-h-screen flex items-center justify-center">
      <p className="text-[var(--text-muted)]">{t("redirecting")}</p>
    </div>
  );
}
