"use client";

import { useEffect, ReactNode } from "react";
import { useRouter } from "@/i18n/routing";
import { useTranslations } from "next-intl";
import { useAuth } from "@/contexts/AuthContext";
import { LoadingOverlay } from "@/components/ui";

interface AdminPageProps {
  children: ReactNode;
}

export function AdminPage({ children }: AdminPageProps) {
  const router = useRouter();
  const { isAuthenticated, isAdmin, isLoading } = useAuth();
  const t = useTranslations('components.adminPage');

  useEffect(() => {
    if (!isLoading) {
      if (!isAuthenticated) {
        router.push("/login");
      } else if (!isAdmin) {
        router.push("/");
      }
    }
  }, [isAuthenticated, isAdmin, isLoading, router]);

  if (isLoading) {
    return <LoadingOverlay message={t('checkingPermissions')} />;
  }

  if (!isAuthenticated || !isAdmin) {
    // Return null while redirecting
    return null;
  }

  return <>{children}</>;
}
