import logging
from pathlib import Path
from synepd.database.models import SynEPDDatabase
from synepd.database.managers import MechanismManager

# Suppress verbose SynReactor logs
logging.getLogger().setLevel(logging.WARNING)

db = SynEPDDatabase(Path("release_v1_full.sqlite"))
mech_mgr = MechanismManager(db)

# 1. Exact Match Test
rsmi_exact = "C[O-].[NH4+]>>CO.N"
print(f"Testing Exact Match: {rsmi_exact}")
predictions_exact = mech_mgr.predict_atom_map(rsmi_exact)
print(f"Result: {predictions_exact}\n")

# 2. Template Match Test (Propanol instead of Methanol)
rsmi_similar = "CCC[O-].[NH4+]>>CCCO.N"
print(f"Testing Template Inference: {rsmi_similar}")
predictions_similar = mech_mgr.predict_atom_map(rsmi_similar)

if predictions_similar:
    print(f"Found {len(predictions_similar)} valid template applications!")
    for p in predictions_similar:
        if p["status"] == "exact_match":
            print(f"  -> Found Exact Match in DB! ID: {p['reaction_id']}")
            print(f"  -> Atom Map: {p['aam']}")
        else:
            print(f"  -> Predicted AAM: {p['predicted_aam']}")
            print(f"  -> Applied RC Hash: {p['wlhash']}")
else:
    print("No templates matched the product output.")
