'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { routing } from '@/i18n/routing';

export default function RootPage() {
  const router = useRouter();

  useEffect(() => {
    try {
      const saved = localStorage.getItem('locale');
      if (saved && routing.locales.includes(saved as typeof routing.locales[number])) {
        router.replace(`/${saved}/`);
        return;
      }
    } catch {
      // localStorage not available
    }

    const browserLangs = navigator.languages || [navigator.language];
    for (const lang of browserLangs) {
      const prefix = lang.split('-')[0].toLowerCase();
      if (routing.locales.includes(prefix as typeof routing.locales[number])) {
        router.replace(`/${prefix}/`);
        return;
      }
    }

    router.replace(`/${routing.defaultLocale}/`);
  }, [router]);

  return null;
}
