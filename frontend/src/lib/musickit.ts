/**
 * MusicKit JS (Apple Music on the web), loaded on demand from Apple's CDN.
 * Docs: https://js-cdn.music.apple.com/musickit/v3/docs/
 */

const MUSICKIT_SRC = "https://js-cdn.music.apple.com/musickit/v3/musickit.js";

/** Where the app waits for the browser hand-off to finish (expo-web-browser auth session). */
export const APP_RETURN_URL = "nomadkaraoke://music-connected?service=apple_music";

interface MusicKitInstance {
  /** Opens Apple's sign-in; resolves with the Music User Token. */
  authorize(): Promise<string>;
}

interface MusicKitGlobal {
  configure(config: { developerToken: string; app: { name: string; build: string } }): Promise<MusicKitInstance>;
  getInstance(): MusicKitInstance;
}

declare global {
  interface Window {
    MusicKit?: MusicKitGlobal;
  }
}

let loading: Promise<MusicKitGlobal> | null = null;

function loadScript(): Promise<MusicKitGlobal> {
  if (window.MusicKit) return Promise.resolve(window.MusicKit);
  loading ??= new Promise<MusicKitGlobal>((resolve, reject) => {
    document.addEventListener("musickitloaded", () => resolve(window.MusicKit!), { once: true });
    const script = document.createElement("script");
    script.src = MUSICKIT_SRC;
    script.async = true;
    script.onerror = () => {
      loading = null;
      reject(new Error("MusicKit JS failed to load"));
    };
    document.head.appendChild(script);
  });
  return loading;
}

/** Load MusicKit JS and configure it with decide's developer token. */
export async function loadMusicKit(developerToken: string): Promise<MusicKitInstance> {
  const musicKit = await loadScript();
  return musicKit.configure({ developerToken, app: { name: "Nomad Karaoke", build: "1.0" } });
}
