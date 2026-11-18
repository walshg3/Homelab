+++
title = "Why Apple TV users should try Infuse for this Plex library"
summary = "Most of this shared Plex library is 4K. Infuse Pro can play many demanding files on Apple TV without making the server convert them first, while the Plex app remains a useful fallback."
slug = "infuse-vs-plex-apple-tv"
date = 2026-08-03T01:22:38-04:00
draft = false
tags = ["plex", "apple-tv", "playback"]
affected_services = ["plex"]
+++

If you use an Apple TV to stream from my shared library over the internet through Plex, I want you to try [Infuse](https://firecore.com/infuse).

You are still using Plex. Infuse connects to the same shared library, reads its metadata, and syncs watched status and playback progress. The difference comes after you press play: Infuse uses its own playback engine on the Apple TV instead of the one in the standard Plex app.

That matters here because most of this library is 4K. Many titles are demanding combinations of MKV containers, high-bitrate HEVC video, HDR or Dolby Vision, lossless audio, and disc-sourced subtitles. The client can determine whether my Plex server sends those streams as they are or has to convert part of the movie before it reaches you.

This is not an argument for deleting Plex. Keep both apps installed. My recommendation is simple: start with Infuse Pro for this library, especially for 4K, and use the Plex app when your internet connection needs a smaller stream or you want a Plex-specific feature.

## Four playback terms worth knowing

Plex uses a few similar-sounding labels. They describe different jobs.

- **Direct Play:** The server sends the original file unchanged. The client opens the container and decodes supported video and audio tracks as needed, then renders any selected subtitles.
- **Direct Stream:** The server keeps compatible streams but repackages them into another container. This is also called remuxing. A pure remux uses little server CPU and does not reduce video quality. Plex can also keep the original video while transcoding only the audio.
- **Transcoding:** The server converts at least one stream into another codec, bitrate, or resolution. Video transcoding is the expensive case and can reduce quality. Audio can be transcoded while the original video stays untouched.
- **Client-side decoding:** The Apple TV decodes compressed media into pictures and sound. This still happens during Direct Play. Decoding on the client is not server transcoding.

That last distinction matters. Infuse does not avoid decoding. It handles more compatibility work on the Apple TV instead of asking Plex Media Server to create a different stream on the fly.

## Why Infuse is useful with awkward files

An MKV file is a container, not a video codec. Inside it might be HEVC video, TrueHD audio, PGS subtitles, or several tracks in different formats. One incompatible piece can change Plex's playback decision.

Plex documents the chain reaction. An incompatible container can trigger a Direct Stream. Unsupported audio can be converted while the video remains untouched. A subtitle the client cannot render may need to be burned into the picture, which requires a full video transcode.

Infuse explicitly supports MKV and publishes a broad format list for its playback engine. The current list includes H.264, HEVC, AV1, VC-1, VP9, MKV, M2TS, and BDMV. Its audio list includes Dolby TrueHD and DTS-HD Master Audio, while its subtitle list includes PGS, VobSub, SRT, VTT, and SSA/ASS. Firecore combines containers, codecs, and disc structures in one list; it does not publish a matrix guaranteeing every combination, profile, level, or bitrate.

That does not make every encode valid or promise that Plex will label every Infuse session "Direct Play." Current Infuse release notes also mention transcoding options for Plex, Emby, and Jellyfin. The defensible claim is narrower: Infuse attempts to stream original content and removes several common reasons for the server to intervene.

Subtitles are a good example. PGS subtitles from a Blu-ray are images, while ASS subtitles can carry positioning and styling. Plex's Apple TV settings document that its default Automatic burn mode burns image-based VobSub, PGS, and SUB/IDX subtitles, plus complex ASS/SSA subtitles. Burning means decoding and re-encoding every video frame on the server. Infuse lists those formats for native playback, so selecting one is less likely to turn an otherwise compatible 4K movie into a server-side video transcode.

The same idea applies to high-bitrate 4K HEVC. Apple lists HEVC Main/Main 10 playback up to 2160p60 for the third-generation Apple TV 4K, and Infuse uses hardware-accelerated decoding on supported Apple hardware. If the file, Apple TV model, network, television, receiver, and HDMI chain all fit within their limits, Infuse can request the original streams instead of a smaller H.264 copy.

High bitrate still means high bitrate. Firecore gives 65 Mbps as a general network-speed guideline for 4K and says faster is better; it is not a maximum file bitrate or a promise that every remux will play. Peak bitrate can be much higher than a file's average. My server's upload, the route between our internet providers, your download speed, and your Wi-Fi or Ethernet connection can all cause buffering. In those cases, Plex's ability to create a smaller adaptive stream is useful rather than evidence of failure.

## HDR and Dolby Vision need a footnote

Infuse officially supports HDR10, HDR10+, HLG, and Dolby Vision Profiles 5 and 8 on compatible hardware. Apple explicitly lists Dolby Vision Profile 5, plus HEVC HDR10+, HDR10, and HLG, for the third-generation Apple TV 4K. Earlier Apple TV generations have different published capabilities.

Do not turn that into "every Dolby Vision file works." Profile 7 UHD Blu-ray remuxes are the awkward case. Firecore's current specification names Profiles 5 and 8, not Profile 7, and neither Apple nor Firecore promises full Profile 7 enhancement-layer behavior in the official documentation cited here. The Dolby Vision profile, base layer, display support, Apple TV generation, Infuse settings, and HDMI chain all matter.

## Lossless audio works, with an Apple TV ceiling

Infuse can decode Dolby TrueHD and DTS-HD Master Audio on Apple TV and send the channel-based result as multichannel LPCM to compatible equipment. Firecore documents output up to 7.1 channels at 24-bit/48 kHz. Sources above 48 kHz are converted to 48 kHz because Apple TV does not support those output sample rates, and a receiver may display "Multichannel PCM" instead of the source codec name.

Object-based audio is the larger catch. Firecore documents Atmos support for E-AC3, the Dolby Digital Plus form common in streaming services. Apple TV does not provide intact TrueHD Atmos or DTS:X bitstream passthrough. Infuse can preserve the lossless channel-based TrueHD or DTS-HD MA result as LPCM, but the receiver does not get the original TrueHD Atmos or DTS:X bitstream and object presentation.

That is a tvOS/Apple TV audio-output limit, not an Infuse setting waiting to be found. If untouched TrueHD Atmos or DTS:X passthrough is the priority, use different playback hardware.

## What you give up

Infuse is free to download, but the formats and features that make this argument interesting generally require Infuse Pro. Firecore offers monthly, yearly, and lifetime options, plus a trial. Prices vary by storefront, region, tax, promotion, and time, so check the current [Apple TV App Store listing](https://apps.apple.com/us/app/infuse/id1136220934) before buying.

Infuse also is not a complete Plex replacement. Its Plex integration covers video libraries, metadata, artwork, watched progress, ratings, On Deck and in-progress lists, remote streaming, versions, collections, and playlists. Plex's own Apple TV documentation lists features such as Auto Adjust Quality, separate home and internet quality controls, Plex Companion, Top Shelf, and controls for skipping detected intros, credits, and DVR commercials. Plex also documents Apple TV support for Live TV and Discover. Availability and entitlement can vary, so test any must-have workflow before changing your household's default app.

Library behavior differs too. Infuse offers Direct Mode for on-demand access to large Plex libraries and Library Mode for a combined local Infuse library with stronger offline browsing. Direct Mode is a library-browsing term, not another name for Direct Play. Either way, you are adding another app, another interface, and another place to check when something behaves strangely.

## The recommendation

If you use an Apple TV, start with Infuse Pro. Most of this library is 4K, and Infuse is more likely to handle its MKV containers, HEVC video, lossless audio, and PGS or ASS subtitles on the Apple TV without asking my server to rebuild the picture first. That usually means less server load and a better chance of preserving the original video quality.

You do not need access to my server tools to compare them. Play the same troublesome title in both apps with the same audio and subtitle selections. Notice whether playback starts cleanly, buffers, loses HDR, or renders the subtitles incorrectly. If Infuse plays it properly, use Infuse for that title.

Keep Plex installed as the fallback. Use it when Infuse cannot play a particular file correctly, when you need a Plex-native feature, or when your connection needs the server to reduce the bitrate. If Infuse buffers at original quality, switching to Plex and selecting a lower remote quality is the right move.

The short version: Infuse first for 4K on Apple TV, Plex when the network or feature set calls for it. You keep access to the same library either way.

## Official sources

- [Infuse features and technical specifications](https://firecore.com/infuse)
- [Infuse on the Apple App Store](https://apps.apple.com/us/app/infuse/id1136220934)
- [Firecore: streaming from Plex, Emby, and Jellyfin](https://support.firecore.com/hc/en-us/articles/360006462093-Streaming-from-Plex-Emby-and-Jellyfin)
- [Firecore: audio options and capabilities](https://support.firecore.com/hc/en-us/articles/217735707-Audio-Options-Capabilities)
- [Firecore: using subtitles](https://support.firecore.com/hc/en-us/articles/215090967-Using-Subtitles)
- [Firecore: testing streaming speeds](https://support.firecore.com/hc/en-us/articles/7551452226967-Testing-Streaming-Speeds)
- [Plex: Direct Play and Direct Stream](https://support.plex.tv/articles/200250387-streaming-media-direct-play-and-direct-stream/)
- [Plex: settings for Apple TV](https://support.plex.tv/articles/settings-plex-for-apple-tv/)
- [Plex: automatically adjust streaming quality](https://support.plex.tv/articles/115007570148-automatically-adjust-quality-when-streaming/)
- [Plex: watching Live TV](https://support.plex.tv/articles/115007689648-watching-live-tv/)
- [Plex: Discover](https://support.plex.tv/articles/discover/)
- [Apple TV 4K (3rd generation) technical specifications](https://support.apple.com/en-us/111839)
