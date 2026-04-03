"use client";

import { useEffect, ReactNode } from "react";
import { useRouter } from "@/i18n/routing";
import { useTranslations } from "next-intl";
import { useAuth } from "@/contexts/AuthContext";
import { LoadingOverlay } from "@/components/ui";

interface ProtectedPageProps {
  children: ReactNode;
}

export function ProtectedPage({ children }: ProtectedPageProps) {
  const router = useRouter();
  const { isAuthenticated, isLoading } = useAuth();
  const t = useTranslations('common');

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.push("/login");
    }
  }, [isAuthenticated, isLoading, router]);

  if (isLoading) {
    return <LoadingOverlay message={t('loading')} />;
  }

  if (!isAuthenticated) {
    // Return null while redirecting
    return null;
  }

  return <>{children}</>;
}
