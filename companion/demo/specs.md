# Beacon laptop judging demo

The laptop runtime is now controlled from the unified dashboard at `/` and
`/demo`. The visual/interaction contract, including Find → inspect → Guide,
is in [../dashboard/specs.md](../dashboard/specs.md). The standalone demo HTML
is retired; runtime/session/safety behavior below remains shared.

## Scope and decisions
Integrate feature/guardian and feature/navigate-target in the current working
tree. Use the laptop microphone, speakers and browser controls; no GPIO button
or dedicated microphone. One stationary demonstration: ask about the live D415
scene, ask to navigate to one clearly visible chair, then open Guardian for
orientation help using the same observation history. Typed scene questions are
the fallback. Guardian uses the existing ElevenLabs agent and laptop audio owner.
Real SMS is excluded from this demo; use the existing fake sender explicitly.
Tactile hardware is optional and is not required to complete this sequence.

## Interaction
The browser provides hold-to-talk, typed question, stop, repeat, local status,
Guardian start/end and Guardian hold-to-talk controls. Start Guardian stops
navigation and pending scene speech. Stop cancels pending replies and navigation.
No simultaneous scene request and Guardian conversation. Cancelled cloud replies
must never start navigation or play audio. Browser scene audio and host Guardian
audio are mutually exclusive. A lost browser heartbeat ends Guardian and stops
navigation. Release/cancel/blur ends recording. No input is sent without action.

## Pipeline and dependencies
Existing web_test HTTP server on localhost -> Gemini scene client -> timestamped
Pi frame -> target box -> existing ROS depth/planner/status path. Preserve all
hazard permission gates; no demo safety bypass. The laptop reuses Session for
observations and GuardianController for agent lifecycle, tools and consent gate.
Guardian needs numpy, sounddevice, ElevenLabs SDK, configured agent, microphone
permission, and laptop speakers. Scene questions work without Guardian extras.
Cloud budgets remain the existing Gemini deadline and Guardian connection timeout.
Stop invalidates cloud requests immediately. A Pi target command already in flight
is serialized before the final remote stop (up to the 6 s transport timeout);
this does not grant movement permission. Browser audio stops immediately.

## Failure behavior and honesty
Browser speaks errors and unavailable status through its local speech fallback.
Missing camera never becomes an observed scene. Missing/stale hazard permission
inhibits physical direction output. Target recognition and route preview can be
shown while guidance is inhibited, with that distinction spoken. Never silently
fall back from live D415 to an old still. Dashboard simulated paths/vitals are
labelled; unavailable measured metrics show unknown. Guardian unavailable is a
recoverable state; scene questions remain usable. No live messages are sent.

## Acceptance
Automated: branch target tests, Guardian tests, typed requests, cancellation,
shared session observation, direction codes 3/4, permission expiry, launcher
without Docker, and browser JavaScript syntax. Manual rehearsal (required on
actual hardware): live frame and stamp, named target depth/status, mic permissions,
ElevenLabs speaker output, Guardian connection and turn, stop while thinking,
Pi disconnect, browser disconnect, and neutral optional hands. Record real
results; software tests do not establish worn safety or real-world latency.

## Open hardware decision
ESP32 availability is pending; the default judging sequence requires no hands.
