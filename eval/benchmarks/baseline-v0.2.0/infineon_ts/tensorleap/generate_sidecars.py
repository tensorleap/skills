"""Generate the latent-space / discriminator / reconstruction .npy sidecars.

Runs the full PyTorch model (model/enhanced_trial_19_full_model_complete.pth) over every
split produced by the repo's WirebondDataset and stores, per split, one row per dataset row:
  latent_space_*.npy          (N, latent_dim)      encoder output z
  discriminator_output_*.npy  (N, num_dcb_classes) equipment-discriminator logits
  reconstruction_output_*.npy (N, 94)              decoder reconstruction
Filenames come from project_config.yaml via infineon.config.CONFIG (which prefixes them with
"<experiment_name>_<sha8>"), and are written to latent_space_and_outputs.output_dir.

Run from the repo root:  poetry run python tensorleap/generate_sidecars.py
"""
import os
import sys

import numpy as np
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tensorleap"))

import vst_model  # noqa: E402
from infineon.config import CONFIG  # noqa: E402
from infineon.wirebonsdataset_page import WirebondDataset  # noqa: E402

SPLITS = {
    "train": dict(train=True, state="labeled"),
    "val": dict(train=False, state="labeled"),
    "unlabeled": dict(train=True, state="unlabeled"),
}


def load_full_model() -> torch.nn.Module:
    # The checkpoint pickles the classes under __main__; expose them there for unpickling.
    main_mod = sys.modules["__main__"]
    for name in dir(vst_model):
        obj = getattr(vst_model, name)
        if isinstance(obj, type):
            setattr(main_mod, name, obj)
    pth_path = os.path.join(ROOT, os.path.splitext(CONFIG["model"]["model_path"])[0] + ".pth")
    model = torch.load(pth_path, map_location="cpu", weights_only=False)
    model.eval()
    return model


def main() -> None:
    out_dir = CONFIG["latent_space_and_outputs"]["output_dir"]
    os.makedirs(out_dir, exist_ok=True)
    model = load_full_model()
    for split, kw in SPLITS.items():
        ds = WirebondDataset(CONFIG["data"]["data_path"], max_seq_len=CONFIG["data"]["max_seq_len"],
                             test_size=CONFIG["split"]["test_size"], random_state=CONFIG["split"]["random_state"], **kw)
        latents, logits_all, recons = [], [], []
        with torch.no_grad():
            for start in range(0, len(ds), 256):
                cur = ds.current_trace[start:start + 256]
                dfm = ds.deformation_trace[start:start + 256]
                recon, _m, _c, _k, _quality, logits = model(cur, dfm, return_dcb_logits=True)
                latents.append(model.encoder(cur, dfm).numpy())
                logits_all.append(logits.numpy())
                recons.append(recon.numpy())
        arrays = {
            "latent_space": np.concatenate(latents).astype(np.float32),
            "discriminator_output": np.concatenate(logits_all).astype(np.float32),
            "reconstruction_output": np.concatenate(recons).astype(np.float32),
        }
        for group, arr in arrays.items():
            fname = CONFIG["latent_space_and_outputs"][group][f"{split}_filename"]
            path = os.path.join(out_dir, fname)
            np.save(path, arr)
            print(f"{split:9s} {group:22s} {arr.shape} -> {path}")


if __name__ == "__main__":
    main()
