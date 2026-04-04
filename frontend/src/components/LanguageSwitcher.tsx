'use client';

import { useState, useRef, useEffect } from 'react';
import { useLocale, useTranslations } from 'next-intl';
import { usePathname, useRouter, routing } from '@/i18n/routing';

const LOCALE_INFO: { code: string; native: string; english: string; flag: string }[] = [
  { code: 'en', native: 'English', english: 'English', flag: '🇬🇧' },
  { code: 'es', native: 'Español', english: 'Spanish', flag: '🇪🇸' },
  { code: 'de', native: 'Deutsch', english: 'German', flag: '🇩🇪' },
  { code: 'pt', native: 'Português', english: 'Portuguese', flag: '🇧🇷' },
  { code: 'fr', native: 'Français', english: 'French', flag: '🇫🇷' },
  { code: 'ja', native: '日本語', english: 'Japanese', flag: '🇯🇵' },
  { code: 'ko', native: '한국어', english: 'Korean', flag: '🇰🇷' },
  { code: 'zh', native: '中文', english: 'Chinese', flag: '🇨🇳' },
  { code: 'it', native: 'Italiano', english: 'Italian', flag: '🇮🇹' },
  { code: 'nl', native: 'Nederlands', english: 'Dutch', flag: '🇳🇱' },
  { code: 'pl', native: 'Polski', english: 'Polish', flag: '🇵🇱' },
  { code: 'tr', native: 'Türkçe', english: 'Turkish', flag: '🇹🇷' },
  { code: 'ru', native: 'Русский', english: 'Russian', flag: '🇷🇺' },
  { code: 'th', native: 'ไทย', english: 'Thai', flag: '🇹🇭' },
  { code: 'id', native: 'Indonesia', english: 'Indonesian', flag: '🇮🇩' },
  { code: 'vi', native: 'Tiếng Việt', english: 'Vietnamese', flag: '🇻🇳' },
  { code: 'tl', native: 'Filipino', english: 'Filipino', flag: '🇵🇭' },
  { code: 'hi', native: 'हिन्दी', english: 'Hindi', flag: '🇮🇳' },
  { code: 'ar', native: 'العربية', english: 'Arabic', flag: '🇸🇦' },
  { code: 'sv', native: 'Svenska', english: 'Swedish', flag: '🇸🇪' },
  { code: 'nb', native: 'Norsk', english: 'Norwegian', flag: '🇳🇴' },
  { code: 'da', native: 'Dansk', english: 'Danish', flag: '🇩🇰' },
  { code: 'fi', native: 'Suomi', english: 'Finnish', flag: '🇫🇮' },
  { code: 'cs', native: 'Čeština', english: 'Czech', flag: '🇨🇿' },
  { code: 'ro', native: 'Română', english: 'Romanian', flag: '🇷🇴' },
  { code: 'hu', native: 'Magyar', english: 'Hungarian', flag: '🇭🇺' },
  { code: 'el', native: 'Ελληνικά', english: 'Greek', flag: '🇬🇷' },
  { code: 'he', native: 'עברית', english: 'Hebrew', flag: '🇮🇱' },
  { code: 'ms', native: 'Melayu', english: 'Malay', flag: '🇲🇾' },
  { code: 'uk', native: 'Українська', english: 'Ukrainian', flag: '🇺🇦' },
  { code: 'hr', native: 'Hrvatski', english: 'Croatian', flag: '🇭🇷' },
  { code: 'sk', native: 'Slovenčina', english: 'Slovak', flag: '🇸🇰' },
  { code: 'ca', native: 'Català', english: 'Catalan', flag: '🏴' },
];

export default function LanguageSwitcher() {
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const t = useTranslations('languageSwitcher');
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const current = LOCALE_INFO.find((l) => l.code === locale) ?? LOCALE_INFO[0];

  // Filter to only configured locales
  const available = LOCALE_INFO.filter((l) =>
    (routing.locales as readonly string[]).includes(l.code)
  );

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [open]);

  function handleSelect(code: string) {
    setOpen(false);
    try { localStorage.setItem('locale', code); } catch {}
    router.replace(pathname, { locale: code });
  }

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        aria-label={t('label')}
        aria-expanded={open}
        className="flex items-center gap-1.5 px-2 py-1 text-sm rounded-md border border-[var(--card-border)] bg-transparent hover:bg-[var(--card)] transition-colors cursor-pointer"
      >
        <span>{current.flag}</span>
        <span className="hidden sm:inline text-[var(--text)]">{current.native}</span>
        <svg className={`w-3 h-3 text-[var(--text-muted)] transition-transform ${open ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="absolute end-0 mt-1 w-56 max-h-80 overflow-y-auto rounded-lg border border-[var(--card-border)] bg-[var(--card)] shadow-lg z-50">
          {available.map((l) => (
            <button
              key={l.code}
              onClick={() => handleSelect(l.code)}
              className={`flex items-center gap-2 w-full px-3 py-2 text-sm text-start hover:bg-[var(--secondary)] transition-colors ${
                l.code === locale ? 'bg-[var(--secondary)] font-medium' : ''
              }`}
            >
              <span>{l.flag}</span>
              <span className="text-[var(--text)]">{l.native}</span>
              {l.code !== locale && (
                <span className="text-[var(--text-subtle)] text-xs ms-auto">{l.english}</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
