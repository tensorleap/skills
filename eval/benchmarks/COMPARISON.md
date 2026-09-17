# Metadata & metrics: `baseline-v0.2.0` vs `new-v0.3.0`

Code-derived rows are parsed from each run's `tensorleap/metadata.py` / `metrics.py` and the harness report JSON. Judgment rows (sources, sliceable, NaN notes) come from a per-run `rubric.json` when present.

## Summary

| fixture | arm | verdict | turns | out tok | cost | concepts | keys | explicit type | sources used | sliceable keys |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|
| cifar10_resnet | baseline-v0.2.0 | PASS | 71 | 49063 | $17 | 2 | 9 | 2/2 | GT, generic image stats | 4 of 9 |
| cifar10_resnet | new-v0.3.0 | PASS | 71 | 52791 | $18 | 5 | 10 | 5/5 | GT (label), documents (CIFAR batch index), domain knowledge (superclass animal/vehicle), generic image stats | 9 of 10 |
| yolov5_visdrone | baseline-v0.2.0 | FAIL\* | 105 | 128594 | $35 | 2 | 10 | 2/2 | GT, generic image stats | 6 of 10 |
| yolov5_visdrone | new-v0.3.0 | PASS\* | 114 | 136666 | $38 | 6 | 14+ | 6/6 | directory (sequence_id parsed from the VisDrone filename), GT-derived (counts, sizes, dominant class, per-class counts), domain knowledge (gt_border_fraction as a truncation proxy; boxes_per_megapixel; letterbox_scale; mean_box_side_model_px = object size at model resolution), generic image stats (mean_brightness) | ~13 of 14 (+ per-class counts) |
| infineon_ts | baseline-v0.2.0 | PASS | 114 | 128858 | $38 | 6 | 38 | 6/6 | documents (parquet columns), GT (shear target family), model-side sidecars (latent/discriminator/reconstruction) | ~22 of 38 |
| infineon_ts | new-v0.3.0 | PASS\* | 139 | 202332 | $66 | 7 | 30 | 7/7 | documents (parquet columns, grouped into sample/wire_bond/record_time), GT (shear_gt), domain knowledge (current_trace + deformation_trace computed features: mean/max/std, hf_energy_ratio, final value, range, trailing-zero fix, length mismatch), model-side (autoencoder reconstruction/latent/discriminator) | ~28 of 30 |
| deeplabv3plus_adas | baseline-v0.2.0 | PASS | 91 | 100909 | $30 | 4 | 24 | 4/4 | documents (split-recipe JSON, Cityscapes vehicle JSON), directory (city/dataset/subset), GT (gt_class_percent), generic image stats | ~15 of 24 |
| deeplabv3plus_adas | new-v0.3.0 | PASS\* | 93 | 141339 | $37 | 4 | 22+ | 4/4 | documents (split-recipe JSON: selection_reason, kitti_drive, vegetation; Cityscapes vehicle JSON: 6 fields), directory (dataset, city, subset_folder), GT (19 per-class pixel fractions as ONE fanned-out concept + labeled_fraction, num_classes_present, dominant_class), domain knowledge (small_object_fraction over a reasoned small-class set; has_gt for the labeled/unlabeled domain split), generic image stats (9), domain knowledge (small_object_fraction over a reasoned small-class set) | ~34 of 45 (revised: has_gt/subset_folder reclassified as noise - they duplicate the platform's own labeled/unlabeled sample state, not new information) |
| plankd | baseline-v0.2.0 | PASS | 95 | 140471 | $36 | 1 | 25 | 1/1 | directory (route/town/weather), documents (measurements JSON), GT-derived, domain knowledge (path length, lateral offset, lidar occupancy) | ~21 of 25 |
| plankd | new-v0.3.0 | *not run* | | | | | | | | |

\* verdict overridden by a manual `VERDICT.md` (e.g. eval re-run after an infrastructure failure).

| arm | passes | Σ turns | Σ out tok | Σ cost |
|---|---:|---:|---:|---:|
| baseline-v0.2.0 | 5/5 | 476 | 547895 | $155 |
| new-v0.3.0 | 4/5 | 417 | 533128 | $159 |

## Per fixture

### cifar10_resnet

| | `baseline-v0.2.0` | `new-v0.3.0` |
|---|---|---|
| metadata concepts → keys | `sample`{label, label_index, filename, source_index}<br>`image_stats`{brightness, contrast, red_mean, green_mean, blue_mean} | `label`{label}<br>`superclass`{superclass}<br>`source_batch`{source_batch}<br>`image`{brightness, contrast, colorfulness, red_mean, green_mean, blue_mean}<br>`sharpness`{sharpness} |
| explicit `metadata_type` | 2/2 | 5/5 |
| loss | categorical_crossentropy | categorical_crossentropy |
| metrics (direction) | accuracy(Upward), gt_confidence(Upward) | accuracy(Upward), gt_class_probability(Upward), confidence(Upward) |
| taxonomy sources used | GT, generic image stats | GT (label), documents (CIFAR batch index), domain knowledge (superclass animal/vehicle), generic image stats |
| sliceable keys (of total) | 4 of 9 | 9 of 10 |
| missing-value policy | explicit types; no missing values in data | explicit types on all 5 concepts |
| domain-knowledge fields | none | superclass (reasoned from label set); sharpness/colorfulness as image-quality proxies |
| noise / duplicates / identifiers | label_index duplicates label; filename, source_index are identifiers; RGB means generic | no identifiers, no duplicates; RGB means generic |
| friction | none | none |
| verdict · turns · cost | PASS · 71 · $17 | PASS · 71 · $18 |

### yolov5_visdrone

| | `baseline-v0.2.0` | `new-v0.3.0` |
|---|---|---|
| metadata concepts → keys | `image`{width, height, aspect_ratio, mean_brightness, resolution}<br>`gt`{num_boxes, num_classes_present, small_object_fraction, mean_box_area_px, dominant_class} | `image`{width, height, letterbox_scale, mean_brightness}<br>`sequence_id`{sequence_id}<br>`gt`{num_boxes, num_classes_present, boxes_per_megapixel, dominant_class}<br>`gt_size`{mean_box_area_px, min_box_area_px, small_object_fraction, mean_box_side_model_px}<br>`gt_border_fraction`{gt_border_fraction}<br>`gt_class_count`{dynamic} |
| explicit `metadata_type` | 2/2 | 6/6 |
| loss | yolov5_loss | yolov5_loss |
| metrics (direction) | detection(DETECTION_METRIC_DIRECTIONS) | detection(METRIC_DIRECTIONS) |
| taxonomy sources used | GT, generic image stats | directory (sequence_id parsed from the VisDrone filename), GT-derived (counts, sizes, dominant class, per-class counts), domain knowledge (gt_border_fraction as a truncation proxy; boxes_per_megapixel; letterbox_scale; mean_box_side_model_px = object size at model resolution), generic image stats (mean_brightness) |
| sliceable keys (of total) | 6 of 10 | ~13 of 14 (+ per-class counts) |
| missing-value policy | explicit types; no None paths | explicit types on all concepts; None for undefined size stats and dominant_class on images without boxes (correct); per-class counts are real zeros |
| domain-knowledge fields | none (the staged subset ships YOLO labels only - class + xywh - so annotation occlusion/truncation fields were NOT available; a geometric truncation proxy was possible but not attempted) | gt_border_fraction (truncation proxy from box geometry), boxes_per_megapixel (density), mean_box_side_model_px (size the detector actually sees after letterboxing) |
| noise / duplicates / identifiers | resolution duplicates width x height; mean_brightness generic | mean_brightness generic; width/height legitimately vary across VisDrone images |
| friction | CLI rewrote malformed leap.yaml; platform build pulled torch 2.14 + CUDA (~8 min); eval FAILED on ES disk watermark, re-run FINISHED | leap projects create overwrote leap.yaml with a blank template (rewrote by hand); push queued behind a foreign project's Evaluate on the shared cluster |
| verdict · turns · cost | FAIL · 105 · $35 | PASS · 114 · $38 |

### infineon_ts

| | `baseline-v0.2.0` | `new-v0.3.0` |
|---|---|---|
| metadata concepts → keys | `sample`{surface_type, surface_code, equipment, equipment_id, dcb_class, package, lot_id, recipe, wire_number, bond_number, shear_sequence_no, timestamp, unique_id, current_length, deformation_length}<br>`gt_shear`{value, value_normed, grade, norm_shear}<br>`trace`{current_peak, current_mean, current_final, current_peak_position, deformation_max, deformation_final, deformation_rise, current_hf_energy_fraction, deformation_hf_energy_fraction}<br>`latent`{norm, phys_m, phys_c, phys_k, phys_omega_n}<br>`discriminator`{pred_class, confidence, entropy}<br>`reconstruction`{mse, max_abs_error} | `sample`{surface_type, equipment_id, package, recipe, bau_no, source_file, labeled}<br>`wire_bond`{wire_number, bond_number, shear_sequence_no}<br>`shear_gt`{shear_value, shear_value_normed, shear_grade}<br>`record_time`{date, hour}<br>`current_trace`{raw_len, raw_mean, raw_max, raw_std, hf_energy_ratio}<br>`deformation_trace`{raw_len, raw_final, raw_range, raw_max, trailing_zero_fixed, len_diff_vs_current}<br>`autoencoder`{reconstruction_mse, latent_norm, discriminator_max_prob, discriminator_entropy} |
| explicit `metadata_type` | 6/6 | 7/7 |
| loss | mse_normed | mse_normed |
| metrics (direction) | abs_error_normed(Downward), abs_error_shear_force(Downward), relative_error_pct(Downward) | abs_error_normed(Downward), abs_error_shear(Downward), relative_error(Downward), predicted_shear_value(Upward), signed_error_shear(Downward) |
| taxonomy sources used | documents (parquet columns), GT (shear target family), model-side sidecars (latent/discriminator/reconstruction) | documents (parquet columns, grouped into sample/wire_bond/record_time), GT (shear_gt), domain knowledge (current_trace + deformation_trace computed features: mean/max/std, hf_energy_ratio, final value, range, trailing-zero fix, length mismatch), model-side (autoencoder reconstruction/latent/discriminator) |
| sliceable keys (of total) | ~22 of 38 | ~28 of 30 |
| missing-value policy | explicit types | explicit types on all 7 concepts; NaN / pd.NA / absent -> None everywhere; hf_energy_ratio None when total power is 0 (undefined, correct) |
| domain-knowledge fields | trace lengths only; no computed trace features (peak current, deformation slope, settling time) | current_trace.hf_energy_ratio, raw_std/raw_max; deformation_trace.raw_final, raw_range, trailing_zero_fixed, len_diff_vs_current - genuine sensor-trace physics the baseline never computed |
| noise / duplicates / identifiers | unique_id, timestamp, lot_id identifiers; equipment/equipment_id and surface_type/surface_code duplicate; 10 sidecar keys need an extra generate_sidecars.py step | source_file borderline identifier (few distinct values, acceptable); no raw unique_id/lot_id/timestamp; record_time{date,hour} replaces the raw timestamp with sliceable fields |
| friction | poetry add code-loader failed on stale scipy 1.6.1 lock -> poetry run pip install; extra sidecar generation script | CLI env flipped to the demo server mid-run (first push landed remotely, FAILED); second push queued ~1h behind a foreign eval, then FAILED with MinIO 403 (expired snapshot URL); agent stopped per infra rule; unchanged code re-pushed by hand as enhanced_trial_19_v2 |
| verdict · turns · cost | PASS · 114 · $38 | PASS · 139 · $66 |

### deeplabv3plus_adas

| | `baseline-v0.2.0` | `new-v0.3.0` |
|---|---|---|
| metadata concepts → keys | `sample`{file_name, city, dataset, subset, labeled, selection_reason, drive, vegetation_percent}<br>`vehicle`{gps_heading, gps_latitude, gps_longitude, outside_temperature, speed, yaw_rate}<br>`image_stats`{width, height, mean, std, mean_r, mean_g, mean_b, dark_fraction}<br>`gt_class_percent`{ignore, num_classes_present} | `source`{dataset, city, subset_folder, has_gt, selection_reason, kitti_drive, manifest_vegetation_percent}<br>`vehicle`{gps_heading, gps_latitude, gps_longitude, outside_temperature, speed, yaw_rate}<br>`image`{original_width, original_height, aspect_ratio, brightness, contrast, mean_red, mean_green, mean_blue, saturation}<br>`gt`{dynamic} |
| explicit `metadata_type` | 4/4 | 4/4 |
| loss | cross_entropy | pixel_cross_entropy |
| metrics (direction) | segmentation(dict), iou_class(dict), mean_confidence(Upward) | pixel_accuracy(Upward), mean_iou(Upward), mean_max_confidence(Upward) |
| taxonomy sources used | documents (split-recipe JSON, Cityscapes vehicle JSON), directory (city/dataset/subset), GT (gt_class_percent), generic image stats | documents (split-recipe JSON: selection_reason, kitti_drive, vegetation; Cityscapes vehicle JSON: 6 fields), directory (dataset, city, subset_folder), GT (19 per-class pixel fractions as ONE fanned-out concept + labeled_fraction, num_classes_present, dominant_class), domain knowledge (small_object_fraction over a reasoned small-class set; has_gt for the labeled/unlabeled domain split), generic image stats (9), domain knowledge (small_object_fraction over a reasoned small-class set) |
| sliceable keys (of total) | ~15 of 24 | ~34 of 45 (revised: has_gt/subset_folder reclassified as noise - they duplicate the platform's own labeled/unlabeled sample state, not new information) |
| missing-value policy | explicit types; vegetation_percent -> None when absent (correct) | explicit types on all 4 concepts; every gt key -> None on unlabeled KITTI samples (absent, correct); dominant_class None when no valid pixels |
| domain-knowledge fields | gt_class_percent (per-class pixel ratio) only | small_object_fraction; full per-class fractions. (has_gt/subset_folder do NOT count as domain knowledge - Tensorleap already integrates no-GT samples as their own unlabeled state natively.) |
| noise / duplicates / identifiers | file_name identifier; width/height constant; 7 generic stats | 9 generic image stats (grew from 8: +aspect_ratio, +saturation); original_width/height vary only between the two datasets; file_name identifier removed; has_gt/subset_folder redundant with the platform's native labeled/unlabeled split |
| friction | poetry add code-loader Pillow failed (Pillow 12 vs py<3.11) -> pip; copied label table to avoid importing repo data module on platform | none reported; ~25 min to push |
| verdict · turns · cost | PASS · 91 · $30 | PASS · 93 · $37 |

### plankd

| | `baseline-v0.2.0` | `new-v0.3.0` |
|---|---|---|
| metadata concepts → keys | `sample`{dataset, route, town, weather_id, frame_idx, route_idx, speed, steer, throttle, brake, command, is_junction, light_state, stop_sign, vehicle_present, bike_present, pedestrian_present, actor_count, occupied_traffic_cells, num_valid_waypoints, gt_path_length, gt_final_lateral_offset, target_point_distance, lidar_occupancy, rgb_mean_brightness} | *not run* |
| explicit `metadata_type` | 1/1 | *not run* |
| loss | waypoint_l1_loss | *not run* |
| metrics (direction) | waypoint_error(dict), traffic_error(dict), junction(dict), traffic_light(dict), stop_sign(dict) | *not run* |
| taxonomy sources used | directory (route/town/weather), documents (measurements JSON), GT-derived, domain knowledge (path length, lateral offset, lidar occupancy) | *not run* |
| sliceable keys (of total) | ~21 of 25 | *not run* |
| missing-value policy | VIOLATION: -1 sentinels for missing town/weather_id (fabricated default) | *not run* |
| domain-knowledge fields | occupied_traffic_cells, num_valid_waypoints, gt_path_length, gt_final_lateral_offset, target_point_distance, lidar_occupancy | *not run* |
| noise / duplicates / identifiers | frame_idx, route_idx identifiers; rgb_mean_brightness generic; all 25 keys under ONE concept | *not run* |
| friction | none reported | *not run* |
| verdict · turns · cost | PASS · 95 · $36 | *not run* |

