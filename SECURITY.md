# Security

## Supported version

Security fixes target the latest published release of SketchNarrator.

## Reporting a vulnerability

Please use the repository host's private security-advisory channel. Do not place credentials, private project media, unpublished presenter assets, or exploit details in a public issue.

## Credential boundary

SketchNarrator reads optional provider credentials only from environment variables. It must not write API keys or access tokens into project JSON, logs, release manifests, test fixtures, or source control.

Edge TTS sends narration text to Microsoft's online speech service. Azure Speech and ElevenLabs send text only after the user explicitly selects and configures those providers. Network source extraction contacts the supplied media host. These network boundaries must remain disclosed in the README and must not be described as offline behavior.

Machine-local `.local`, `.venv`, cache, egg-info, and project output directories are excluded from Git and the release manifest, but ignore rules do not protect a manually created whole-directory archive. Use the manifest/Git file set for release, or run the explicit whole-tree check before creating such an archive. Public text hygiene checks cover Windows paths using either slash style plus provider-specific and Bearer credential forms.

Provider base URLs must not contain URL user information such as `user:password@host`. VoiceStudio rejects those URLs before making a request or writing metadata, and records only a normalized, credential-free server root.

Source intake also rejects URLs containing user information before acquisition. Source labels omit URL user information, query strings, and fragments; the public extraction workflow applies the same URL redaction to captured subprocess output before displaying or logging it. Local source labels use the filename. Media paths and source content needed for processing remain private project data, and existing project records are not rewritten automatically.

The composition panel binds to a loopback address by default. Non-loopback binding requires explicit `--allow-lan` consent. The session token is carried only in the initial URL fragment, removed from browser history immediately, and exchanged for an HttpOnly, `SameSite=Strict` session cookie. The cookie keeps authenticated API and media access working after a page refresh; the initial exchange still requires the token header. In both loopback and LAN modes, every `/project/*` request requires an authenticated same-origin session, so unauthenticated or cross-origin browser contexts cannot read project images, audio, previews, subtitles, or JSON. Project file references are resolved inside the selected project root before they are read, and project-derived labels and timeline values are rendered as text or finite numbers rather than trusted markup. Panel responses also deny framing and cross-origin resource reuse, disable MIME sniffing, and apply a restrictive local-only content policy. The static panel shell and public style-reference images contain no project data and remain readable without authentication.

## Installation and optional network operations

The `doctor` launcher route does not install packages or contact online services. Host capability declarations are not proof of execution; directory checks are permission estimates. Default dependency preparation contacts the configured Python package index. Edge TTS sends narration text online. Optional Azure/ElevenLabs send text to their services; VoiceStudio sends it to the configured endpoint. Source extraction contacts the media source and may download ASR models; optional local alignment may also download models. Image generation uses the host's configured image service. These operations are not an offline workflow.

The bootstrap pip command disables its persistent download cache; temporary files, model caches, and host caches may still live outside the Skill. A virtual environment isolates installed Python packages, not all filesystem or network activity.

VoiceStudio does not log server error bodies or health response bodies, rejects redirects, and requires HTTPS when sending credentials outside loopback. Optional service diagnostics redact known environment secrets and limit displayed text; this is defense in depth, not a guarantee about arbitrary third-party SDKs. LAN panel mode uses HTTP and is not encrypted; use loopback by default and do not expose LAN mode to untrusted networks. Cookie and origin checks do not provide transport encryption.
