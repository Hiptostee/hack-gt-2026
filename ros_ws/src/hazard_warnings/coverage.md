# COVERAGE-01 — wearable calibration record

**Status: NOT MEASURED. No wearable coverage or latency claim is established.**
The default `config/hazards.yaml` deliberately reports sensing unavailable and
prevents navigation. Synthetic test values must never be copied as wearer measurements.

Before enabling `calibrated` and `coverage_verified`, record:

| Measurement | Actual result |
| --- | --- |
| Date, operator, wearer, mounting fixture | Pending |
| D415 serial, image profile, aligned optical frame | Pending |
| Depth encoding and verified metres-per-unit | Pending |
| Camera optical origin in wearer_base (metres) | Pending |
| Optical-to-body rotation (roll/pitch/yaw, radians) | Pending |
| IMU-to-body rotation, upright acceleration and rotation noise | Pending |
| Wearer half-width, clearance margin | Pending |
| Torso minimum, head minimum/maximum height | Pending |
| Speaker, volume, warning comprehension | Pending |
| Allowed posture/motion envelope and approach speed | Pending |

`wearer_base` has x forward, y left, z up, origin at the floor projection of the
wearer in the measured pose. The example optical rotation in the YAML is for
a level forward-facing camera only. Measure the IMU rotation independently;
the existing SLAM camera-to-IMU transform is not used by this detector.

For each distance, test torso/head fixtures at left, center and right, including
leaning and turning. Record observed and unobserved regions separately.

| Distance | Torso coverage | Head coverage | Floor coverage | Thin fixture thickness | Blind zones |
| --- | --- | --- | --- | --- | --- |
| 0.5 m | Pending | Pending | Unsupported | Pending | Pending |
| 1.0 m | Pending | Pending | Unsupported | Pending | Pending |
| 1.5 m | Pending | Pending | Unsupported | Pending | Pending |
| 2.0 m | Pending | Pending | Unsupported | Pending | Pending |

Stage A supports only generic observed upper-body obstructions within the
configured corridor. Floor changes, doors/passages and semantic names remain
disabled. Glass, reflections, darkness, blank surfaces and near-range holes
require separate negative/failure trials; valid surrounding pixels do not prove
that an individual thin or transparent obstacle is observable.

Attach the fixture results and full-load measurements from spec §10: missed
warnings, nuisance alerts, capture-to-tone and first-word median/p95/worst,
sample counts, CPU/temperature, and camera/IMU/producer/audio loss. Choose the
approach speed only after the measured range/latency calculation in spec §6.
