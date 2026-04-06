import { defineRouting } from 'next-intl/routing';
import { createNavigation } from 'next-intl/navigation';

export const routing = defineRouting({
  locales: [
    'en', 'ar', 'ca', 'cs', 'da', 'de', 'el', 'es', 'fi', 'fr',
    'he', 'hi', 'hr', 'hu', 'id', 'it', 'ja', 'ko', 'ms', 'nb',
    'nl', 'pl', 'pt', 'ro', 'ru', 'sk', 'sv', 'th', 'tl', 'tr',
    'uk', 'vi', 'zh'
  ],
  defaultLocale: 'en'
});

export const { Link, redirect, usePathname, useRouter } =
  createNavigation(routing);
