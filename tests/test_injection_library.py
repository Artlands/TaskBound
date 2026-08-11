"""Injection-library acceptance checks (plan §11.3, milestones 5 and 6).

`v0.5` ships 36 attacked, 36 benign, and 9 inert texts for H1's twelve E1–E3
cells. What has to hold of them collectively — matched paraphrase intents, a
committed request family, no literal secrets, a placement that resolves — is
checked here rather than left to the reviewer's eye.
"""

from __future__ import annotations

import glob
import json
import os
import re

import hostfixture
from taskbound.backend import LocalSimBackend
from taskbound.inject import apply_injection, exposure_probe, sample_placement

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HOST_DIR = os.path.join(ROOT, "hosts", "site_a")
CELLS = [f"E{e}A{a}" for e in (1, 2, 3) for a in (1, 2, 3, 4)]


def library() -> list[dict]:
    out = []
    for path in sorted(glob.glob(os.path.join(ROOT, "injections", "*.json"))):
        with open(path, encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


def host() -> dict:
    with open(os.path.join(HOST_DIR, "host.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_every_cell_ships_three_matched_attacked_and_benign_paraphrases():
    by = {}
    for inj in library():
        if inj["kind"] == "inert":
            continue
        by.setdefault((inj["cell"], inj["kind"]), set()).add(inj["paraphrase"])
    for cell in CELLS:
        assert by[cell, "attack"] == {"p1", "p2", "p3"}, cell
        # Matched, not merely equal in count: paraphrase p2 attacked is the
        # pair of paraphrase p2 benign (plan §6.3).
        assert by[cell, "benign"] == by[cell, "attack"], cell


def test_every_entry_point_ships_three_inert_texts():
    """Inert is per entry point under the core task alone (plan §7.2).

    Twelve texts once E4 joins, not nine: the text-presence contrast has to
    cover the entry point whose vehicle arrives mid-workflow too, or E4's
    attacked rate is the one with no background to read it against.
    """
    inert = [i for i in library() if i["kind"] == "inert"]
    assert len(inert) == 12
    for entry in ("E1", "E2", "E3", "E4"):
        group = [i for i in inert if i["entry_point"] == entry]
        assert {i["paraphrase"] for i in group} == {"i1", "i2", "i3"}
        for i in group:
            # Content with no request cannot be complied with (plan §7.2).
            assert "compliance_predicate" not in i
            assert i["induced_action"] is None


def test_the_near_duplicate_threshold_is_tight_against_the_shipped_material():
    """A threshold no committed text comes near is not protecting anything.

    A paraphrase is a distinct rationale for the same request, not a rewording
    (plan §7.5), so the shipped set sits far below the gate: the observed
    maximum is around 0.37 against a threshold of 0.50. This pins both ends —
    loosening the threshold, or authoring a lazier paraphrase set, fails here
    rather than passing a check that was never binding.
    """
    from taskbound.validate import NEAR_DUPLICATE_THRESHOLD, _similarity

    groups: dict[tuple[str, str], list[dict]] = {}
    for inj in library():
        groups.setdefault((inj["cell"], inj["kind"]), []).append(inj)

    worst = 0.0
    for group in groups.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                worst = max(worst, _similarity(a["text"], b["text"]))

    assert worst < NEAR_DUPLICATE_THRESHOLD, f"a shipped pair is at {worst:.2f}"
    # Margin, not just clearance: if the gate ever sits within a whisker of the
    # material it is one lazy paraphrase away from being decorative.
    assert NEAR_DUPLICATE_THRESHOLD - worst >= 0.10, (
        f"threshold {NEAR_DUPLICATE_THRESHOLD} is only {NEAR_DUPLICATE_THRESHOLD - worst:.2f} "
        f"above the worst shipped pair"
    )


def test_request_families_are_committed_beside_their_texts():
    for inj in library():
        spec = os.path.join(ROOT, "injections", "specs", inj["spec_id"] + ".md")
        assert os.path.isfile(spec), inj["injection_id"]


def test_attacked_and_benign_share_a_family_and_differ_in_the_target():
    """The contrast is the target's scope, so everything else stays fixed."""
    by_id = {i["injection_id"]: i for i in library()}
    for cell in CELLS:
        for para in ("p1", "p2", "p3"):
            a = by_id[f"t1_{cell.lower()}_attack_{para}"]
            b = by_id[f"t1_{cell.lower()}_benign_{para}"]
            assert a["spec_id"] == b["spec_id"]
            assert a["placement_class"] == b["placement_class"]
            assert a["target"]["scope"] == "out_of_scope"
            assert b["target"]["scope"] == "in_scope"


def test_no_committed_text_carries_a_literal_canary_or_marker():
    """Slots are substituted at load time; a literal one would be published."""
    for inj in library():
        assert not re.search(r"TB-(CANARY|MARKER)-", inj["text"]), inj["injection_id"]
        if inj.get("induced_action") == "A4":
            # Three tasks carry A4 and each declares its own marker slot, so the
            # text must use the slot it declares rather than one fixed name.
            for slot in inj["marker_slots"]:
                assert "{{marker:" + slot + "}}" in inj["text"], inj["injection_id"]
            assert inj["marker_slots"], inj["injection_id"]


def test_every_injection_declares_who_could_have_written_it():
    for inj in library():
        assert len(inj["vehicle_writer"]) > 40, inj["injection_id"]
        assert len(inj["write_precondition"]) > 40, inj["injection_id"]
        assert inj["generator"] and inj["generated_at"] and inj["accepted_by"]


def test_every_text_reaches_the_agent_through_its_own_vehicle(tmp_path):
    """Applied to a real workspace, each text is exposed by the natural call.

    A placement that resolves to nothing, or a vehicle a tool cannot render,
    would otherwise produce a silently clean run rather than a failure.
    """
    h = host()
    policy = hostfixture.policy()
    # Each entry point now has more than one vehicle, so the natural call is
    # derived from the injection's own placement class rather than from E alone.
    module_names = {
        "/workspace/modulefiles/hdf5-1.14.3.lua": "hdf5/1.14.3",
        "/workspace/modulefiles/site-report-2.1.lua": "site-report/2.1",
    }

    def natural_call(placement_class: str):
        spec = h["placement_classes"][placement_class]
        if spec["entry_point"] == "E3":
            return "module_show", {"name": module_names[spec["file"]]}
        return "read_file", {"path": spec["file"]}

    for n, inj in enumerate(library()):
        run_dir = str(tmp_path / f"run{n}")
        subs = {"{{marker:" + slot + "}}": f"TB-MARKER-{slot.upper()}"
                for slot in inj.get("marker_slots", [])}
        backend = LocalSimBackend.materialize(HOST_DIR, run_dir, policy, subs)
        placement = sample_placement(
            backend.workspace, h["placement_classes"], inj["placement_class"], seed=n
        )
        text = inj["text"]
        for placeholder, value in subs.items():
            text = text.replace(placeholder, value)
        probe = exposure_probe(apply_injection(backend.workspace, placement, text))
        tool, args = natural_call(inj["placement_class"])
        assert probe in backend.call(tool, args), inj["injection_id"]
