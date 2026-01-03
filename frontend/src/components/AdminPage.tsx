"use client";

import { useEffect, ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { LoadingOverlay } from "@/components/ui";

interface AdminPageProps {
  children: ReactNode;
}

export function AdminPage({ children }: AdminPageProps) {
  const router = useRouter();
  const { isAuthenticated, isAdmin, isLoading } = useAuth();

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
    return <LoadingOverlay message="Checking permissions..." />;
  }

  if (!isAuthenticated || !isAdmin) {
    // Return null while redirecting
    return null;
  }

  return <>{children}</>;
}
