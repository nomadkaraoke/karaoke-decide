# KaraokeHunt: Song Finder

## *Find your own personal best songs to sing at karaoke\!*

Primary use case: help people have a great experience singing karaoke, by building the ideal playlist of songs for them and their friends, every time\!

**Short one-liner**: find your perfect karaoke songs	  
**Short summary**: create karaoke playlists based on your music history and what you’ve enjoyed singing.  
no karaoke version of a song you love? generate it\!

**Non-functional requirements:**

* Infrastructure: PaaS, serverless and scalable, e.g. **Firebase**  
* Backend language: preferably **Golang**, TypeScript or Python  
* Cross-platform mobile UI: fast and native experience for Android & iOS, e.g. using **Flutter** 

**V0 functionality** (*minimum usable release*)

* **✅User Auth**: User registration (with email), with optional name, phone, bio and profile photo  
* **✅Cross-Platform**: usable app on Android, iOS and Web  
* **✅Open-source**: free, [public code repository](https://github.com/karaokenerds/karaokehunt-app) with no secrets and easy setup / run instructions  
* **✅Fetch Community Karaoke Song Catalog**: fetch, store locally, and allow update of data  
* **✅Searchable Song List**: Show the user a list of tracks from the community karaoke catalog, with simple text search to find artist or song title  
* **✅Personal Playlist Creation**: Allow the user to add songs from the song list to a playlist, which is persisted across app launches but can be cleared	

**V1 functionality** (*minimum* lovable product, or *single player mode*):

* **User Profiles with social login**: User registration (with email or phone number), with name and profile photo  
  * **✅**User profile edit (to edit name, photo or other personal details in the future)  
  * 🚧Might be easiest to use music service(s) as identity provider (e.g. one-click “Log in with Spotify”, “Log in with Apple Music” and “Log in with Google” buttons), but would need to normalize across the different services

* **🚧Fetch Personal Listening History**: Allow user to connect their personal [Last.fm](http://Last.fm), [Spotify](https://developer.spotify.com/console/get-current-user-saved-tracks/), [Apple Music](https://developer.apple.com/documentation/applemusicapi/get_recently_played_tracks), [Youtube Music](https://ytmusicapi.readthedocs.io/en/latest/reference.html#ytmusicapi.YTMusic.get_library_songs) accounts (at least one, potentially more than one). Fetch as much music library and listening history data as possible from the above services, to populate a database of tracks the user has listened to, ideally with listen counts

* **✅Fetch Combined Karaoke Song Catalog**: Fetch, store, and periodically update data from multiple Karaoke data APIs to form a combined karaoke song catalog (Karaoke Nerds alone may be complete enough data)  
  * [Karaoke Nerds](https://karaokenerds.com/Browse/Artist)  
  * [OpenKJ](https://db.openkj.org/search?type=Artist&searchstr=Panic%20At%20The%20Disco)  
  * [iCroons](http://www.icroons.com/default.aspx)

* **🚧Fetch Song Analysis Data:** For every song in the combined catalog:  
  * Fetch and store basic track metadata (e.g. from the [Spotify track info](https://developer.spotify.com/documentation/web-api/reference/#/operations/get-track) API and/or the [Last.fm](https://www.last.fm/api/show/track.getInfo) track info API), such as:  
    * Duration  
    * Publish Date  
    * Listeners / Popularity  
    * Genres / Tags ([last.fm](http://last.fm) tag data may serve other use cases than just genre alone)

  * Fetch and store song analysis data (e.g. from the [Cyanite](https://cyanite.ai/) API and/or [Spotify Audio Features](https://developer.spotify.com/documentation/web-api/reference/#/operations/get-several-audio-features) API), such as:  
    * Genre & Subgenres  
    * Key (and/or pitch, for vocal range filtering)  
    * BPM  
    * Energy Level  
    * Emotional Profile  
    * Danceability  
    * Instrument Presence  
    * Primary Voice Gender  
    * Voice Presence Profile (e.g. how vocal is the track)  
    * Mood Tags (e.g. aggressive, uplifting, energetic, etc.)

  * Flag whether the song is an "all time popular karaoke song" in the database, by manually looking at a few online lists of top karaoke songs, e.g.  
    * [https://www.billboard.com/lists/best-karaoke-songs-all-time/](https://www.billboard.com/lists/best-karaoke-songs-all-time/)  
    * [https://www.timeout.com/music/the-50-best-karaoke-songs-ever](https://www.timeout.com/music/the-50-best-karaoke-songs-ever)  
    * [https://www.buzzfeed.com/evelinamedina/best-karaoke-songs](https://www.buzzfeed.com/evelinamedina/best-karaoke-songs)  
    * [https://www.karafun.com/karaoke/playlist/global-top-100/](https://www.karafun.com/karaoke/playlist/global-top-100/)

* **🚧Filterable, Sortable Song List**: Show the user a list of tracks from the combined karaoke catalog, sorted by how many times they have personally listened to that song  
  * Allow the user to filter and sort this song list by a wide variety of things, e.g. all of the song analysis fields collected above, and the "all time popular" flag

* **🚧”Singable” Filter (Vocal Range Detection)**: Allow the user to sing into the microphone (e.g. singing a full scale from the lowest they can reach to the highest) to detect their vocal range\!  
  * Once saved, allow the user to filter for only songs within X steps of their vocal range \- in theory filtering for vocal range stretch: 0 should **only show songs the user can actually sing**  
  * Collect feedback in the post-song survey about whether the song was comfortable to sing  
  * Show % of people with a similar (+/- 2 steps) vocal range who said this song was comfortable to sing \- essentially **crowdsourcing “singability” data**

* **🚧Personal Playlist Creation**: Allow the user to add songs from the song list to a playlist, which can be saved and cleared  
  * ✅Initial (single) playlist addition & saving  
  * **🚧**User should be able to save multiple playlists, e.g. so they can build up playlists for different moods, events or groups of people

* **🚧Post-Song Survey**: On the playlist screen, allow the user to check off which songs they have sung as they go, and capture a few additional bits of info for each one sung.  
  * Users should also be able to filter by these additional fields (e.g. only show songs which have been sung before) on the filterable song list page.  
  * Example survey questions (these need more thought put into them):  
    * Was it generally good or bad to sing?  
    * What difficulty level was it? (1 to 5\)  
    * Do you need to be warmed up to sing this?  
    * Was it a crowd-pleaser / did other people join in?

**V2 functionality** (*nice to haves*, or *connected multiplayer mode*)**:**

* **Friend Filter**: Ability to add other app users as friends and create a "karaoke crew" (list of people going to karaoke together), allowing the user to filter the song list for songs in those *other peoples'* listening histories  
  * This would allow one user to find songs to sing which multiple people (or specific people) in the group also know and like\!

* **Group Playlists**: Ability to create a *group playlist* with friends  
  * Once shared and accepted by all in the group, everyones' app would show the same playlist and progression through it would be synchronized (would need a local workaround when offline)  
  * For auto-generated playlists, the app would go through each singer in order, so every person gets the same number of chances to sing and nobody is waiting too long  
  * Songs in the playlist would be tagged for specific people to sing, e.g. after tapping "finished" on a song it could announce "Up next: James singing Starlight by Muse" etc.

* **Auto-generated Playlists**: Ability for the app to auto-generate a recommended playlist, based on the user inputting a few choices:  
  * Duration of karaoke session  
  * Number of people in group  
  * Preferred moods / genres (multi-select)

* **Community Playlists**: Allow people to share and discover karaoke playlists with the community, e.g. “pop punk bangers”, “emo trash”, “just got dumped” etc.

* **Karaoke Bar Filter**: Ability to filter the song list by only songs which are available at a specific karaoke bar \- this would require a bunch of additional things, such as:  
  * Initial database of karaoke businesses, with name, location and list of songs they provide (one-\>many relation with the global combined catalog already implemented above)  
  * Ability for user to easily submit a new karaoke bar to be added to the app, e.g. name, location, web address if available, song catalog link or PDF if available  
    * For now this would just come through to me to process and add to the database manually, but in a future version we might be able to find a way to automate it, e.g. a flow where the user manually selects a few songs which are in the karaoke places' songbook, and we could then look those up in our DB to see which song provider is most likely being used, offer that as a suggestion, let the user check etc.  
  * Map & address search for user to select the bar they're at, with this then filtering the song list  
  * Ability for user to save a list of their favorite karaoke bars for easy selection next time they open the app

* **Transmit Songs to System**: Ability to integrate with specific karaoke bars' own technology to transmit the playlist of songs into their own system, to avoid users needing to select their songs both in Karaokay and the bars' system.  
  * This is probably one for further down the line if we can build a successful app and demonstrate the value of it, then reach out to karaoke businesses and try to partner with them.  
  * It could be worthwhile to implement some basic stubs to make this integration easier though, e.g. an API method which returns a simple list of all of the tracks in a user-created playlist, given a user email address or something (with the assumption that a future integration might call this method to fetch a customers' song list and load it into the internal karaoke system)  
  * A first version of this could be designed specifically to work with YouTube (e.g. for at-home karaoke), using youtube video IDs for tracks already in the KaraokeNerds database ([example here](https://karaokenerds.com/Song/Moving%20On%20Up/M%20People)) and the YouTube [loadPlaylist](https://developers.google.com/youtube/iframe_api_reference#Playlist_Queueing_Functions) API call\!

**V3 functionality** (*fully unrestricted karaoke*):

* **Easy Karaoke Version Creation, by human or automation**:  
  * When a user searches for something and doesn’t find the results they want, they can request any (non-karaoke version) song which is available on YouTube to be made into a karaoke version\!  
  * They’ll be prompted on whether they want an auto-generated version (medium quality) or a high quality version created by a human \- if they select a human version, the app will make it super easy for them to request one to be created by the community, with an easy way for them to commission (pay a larger amount for priority creation) or tip (pay a smaller amount with no guarantees of timeline) \- we’ll need to figure out specifics to make this *fair* of course, but hopefully this will make it easier for karaoke creators to make money from the art\!  
  * If the user chooses the medium-quality *fully automated* version:  
    * After 5-10 minutes, there will be a freshly created karaoke version of that song, published to YouTube under the Nomad Karaoke channel, ready for playback.  
    * The user will get a push notification via the app when their requested karaoke song is ready to sing\!  
    * To accomplish this, the app will need to:  
      * Fetch the requested YouTube video using [yt-dlp](https://github.com/yt-dlp/yt-dlp) and extract the audio to wav using ffmpeg  
      * Run that audio through an ML-based vocal isolation model tuned for karaoke (e.g. [UVR-MDX-NET Karaoke 2](https://github.com/Anjok07/ultimatevocalremovergui/blob/master/models/MDX_Net_Models/model_data/model_name_mapper.json#L12)) to get high quality instrumental audio without lead vocals but retaining backing vocals  
      * Run the lead vocal track through [whisper-timestamped](https://github.com/linto-ai/whisper-timestamped) to generate a time-synced lyrics file, and *correct* this by also fetching lyrics from a non-AI source (e.g. spotify, genius) and attempting to match up with the whisper-heard lyrics whilst maintaining timestamps  
      * Generate a new video file using a background image, with the synced lyrics “burned” into the video at the correct timestamps  
      * Publish this video to YouTube  
    * There’s actually already a step by step tutorial on how to do this, [here](https://www.digitalocean.com/community/tutorials/how-to-make-karaoke-videos-using-whisper-and-spleeter-ai-tools)\! And research / experimentation from Max Hilsdorf [here](https://medium.com/mlearning-ai/zero-shot-song-lyrics-transcription-using-whisper-3f360499bcfe)\!  
    * There’s also a bunch of existing resources on automatic subtitles, e.g. [AutoCaption](https://blog.paperspace.com/automatic-video-subtitles-with-whisper-autocaption/) and [yt-whisper](https://github.com/m1guelpf/yt-whisper)  
    * Andrew notes: compare quality of word-level timestamps from Amazon Transcribe API vs. Whisper-Timestamped

* **V4 Functionality** (TV app)  
  * **Support Android/Apple TV**: Provide fully functional TV version of the app

  * **Choose Best Version**: Offer additional functionality to choose a version of a track to play on YouTube, with icons/explanation about which versions are the “best” (e.g. divebar community, if available, otherwise whatever commercial)

  * **“Auto-play Best”:** Play entire playlist of “best” youtube videos, embedded within the app? Or if embedding isn’t possible, launch each video in the YouTube app after the previous one finishes? 

* V5 Functionality (competition mode)  
  * Rank performance using audio from app microphone\! (like singstar but more relaxed / unscientific for fun)  
  * Rank other social friends performances\!  
  * Gamification to make it go viral\!

**V5 Functionality / April Fool’s Day prank:**

* “KK Slider” Mode 😂- generates KK Slider version of every song requested, e.g.  [https://youtu.be/lz\_o6dmJuH8?si=pfDiJKhbXkw9wD02\&t=42](https://youtu.be/lz_o6dmJuH8?si=pfDiJKhbXkw9wD02&t=42)

