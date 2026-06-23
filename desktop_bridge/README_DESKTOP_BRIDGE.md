# SHIMS Android -> Predator Desktop Bridge

This is the recommended full-power mode.

1. Extract `shims_v11_reference/shims_omni_enterprise_v11_unified_final.zip` on the Predator.
2. Run SHIMS v11 with host binding:

```bat
cd /d E:\shims_final_omni_enterprise_2026
install_windows.bat
start_ollama.bat
.venv\Scripts\python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8010
```

3. Find the Predator Wi-Fi IP:

```bat
ipconfig
```

4. In Android app Settings, set backend:

```text
http://YOUR_PC_IP:8010
```

This gives the phone full SHIMS Omni v11 capability: Ollama model pull/list/select on the Predator, image generation, PDF/PPT, Enterprise bridge, GST, R&D/QC workflows, and stronger local models.
