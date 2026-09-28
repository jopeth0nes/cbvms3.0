"""Deterministic ownership/evidence checks; no camera, model, database, or Tk."""

from dataclasses import FrozenInstanceError
import unittest

from core.live_state import (
    CHECKING_UNIFORM, IDENTIFYING, IDENTITY_UNCERTAIN, SUSPECTED_VIOLATION,
    UNIFORM_COMPLIANT, UNIFORM_NOT_ASSESSED, UNKNOWN_PERSON,
    FrameContext, LiveConfig, LiveState, associate_faces_to_bodies, validate_torso,
)


def row(sid="A", x=100, *, appearance=0, uniform="correct_uniform", **overrides):
    embedding = [0., 0., 0., 0.]
    embedding[appearance] = 1.
    result = dict(box=[x, 30, x + 60, 100], embedding=embedding,
                  matched=bool(sid), student_id=sid, name=f"Student {sid}", gender="Male",
                  student_status="Enrolled", discipline_eligible=True,
                  body_index=0, body_box=[x-30, 20, x+90, 360], association_valid=True,
                  torso_box=[x-20, 110, x+80, 230], torso_valid=True,
                  uniform_available=True, uniform_label=uniform, uniform_conf=.90)
    result.update(overrides)
    return result


def update(state, frame_id, rows, *, at=None, generation=1, now=None):
    at = frame_id * .25 if at is None else at
    return state.update(FrameContext(generation, ("camera", frame_id), at), rows,
                        now=at + .10 if now is None else now)


class BodyOwnershipTests(unittest.TestCase):
    def test_separated_people_associate_one_to_one_in_any_body_order(self):
        faces = [[110, 20, 150, 80], [410, 20, 450, 80]]
        bodies = [[80, 0, 190, 350], [380, 0, 490, 350]]
        self.assertEqual([a.index for a in associate_faces_to_bodies(faces, bodies)], [0, 1])
        self.assertEqual([a.index for a in associate_faces_to_bodies(faces, bodies[::-1])], [1, 0])

    def test_foreground_and_background_use_head_geometry(self):
        faces = [[240, 40, 340, 180], [60, 100, 90, 145]]
        bodies = [[170, 10, 430, 600], [40, 80, 115, 310]]
        self.assertEqual([a.index for a in associate_faces_to_bodies(faces, bodies)], [0, 1])

    def test_two_faces_cannot_independently_claim_one_body(self):
        associations = associate_faces_to_bodies(
            [[100, 20, 140, 80], [160, 20, 200, 80]], [[60, 0, 240, 350]])
        self.assertTrue(all(not a.valid for a in associations))
        self.assertTrue(all("same body" in a.reason for a in associations))

    def test_overlapping_body_ambiguity_rejected(self):
        associations = associate_faces_to_bodies([[100, 20, 140, 80]],
                                                 [[60, 0, 180, 350], [65, 0, 185, 350]])
        self.assertFalse(associations[0].valid)
        self.assertIn("Ambiguous", associations[0].reason)

    def test_rejected_ambiguous_face_still_blocks_its_competing_body_claim(self):
        associations = associate_faces_to_bodies(
            [[100, 20, 140, 80], [155, 20, 195, 80]],
            [[60, 0, 210, 350], [30, 0, 180, 350]])
        self.assertTrue(all(not item.valid for item in associations))

    def test_no_largest_body_or_face_only_fallback(self):
        for bodies in ([], [[350, 0, 640, 480]], [[0, 0, 640, 70]]):
            self.assertFalse(associate_faces_to_bodies([[100, 100, 140, 180]], bodies)[0].valid)

    def test_torso_quality_visibility_and_other_person_contamination(self):
        face, body, torso = [100, 20, 160, 90], [60, 0, 200, 400], [80, 110, 180, 230]
        self.assertTrue(validate_torso(face, body, torso, (480, 640))[0])
        for bad in ([80, 60, 180, 180], [80, 110, 95, 125], [80, 450, 180, 550],
                    [350, 110, 450, 230], [float("nan"), 0, 5, 6]):
            self.assertFalse(validate_torso(face, body, bad, (480, 640))[0])
        self.assertFalse(validate_torso(face, body, torso, (480, 640),
                                        other_faces=[[120, 140, 170, 210]])[0])
        self.assertFalse(validate_torso(face, body, torso, (480, 640),
                                        other_bodies=[[120, 0, 300, 400]])[0])


class LiveStateTests(unittest.TestCase):
    def settled(self, rows=None, config=None):
        state = LiveState(config)
        rows = [row()] if rows is None else rows
        for frame_id in range(1, 5):
            result = update(state, frame_id, rows)
        return state, result

    def test_two_students_different_uniforms_keep_authoritative_owner(self):
        state, result = self.settled([row(), row("B", 350, appearance=1, uniform="wrong_uniform")])
        good, wrong = result
        self.assertEqual((good.student_id, good.state, good.accepted_categories), ("A", UNIFORM_COMPLIANT, ()))
        self.assertEqual((wrong.student_id, wrong.state, wrong.accepted_categories),
                         ("B", SUSPECTED_VIOLATION, ("wrong_uniform",)))
        # Detector ordering and body indices are not identity or evidence keys.
        after = update(state, 5, [row("B", 355, appearance=1, uniform="wrong_uniform", body_index=0), row(x=105, body_index=1)])
        self.assertEqual([a.track_id for a in after], [wrong.track_id, good.track_id])
        self.assertEqual([a.state for a in after], [SUSPECTED_VIOLATION, UNIFORM_COMPLIANT])

    def test_appearance_preserves_tracks_through_crossing(self):
        state = LiveState()
        result = update(state, 1, [row("A", 100), row("B", 300, appearance=1)])
        ids = [a.track_id for a in result]
        for frame, positions in enumerate(((155, 245), (205, 195), (265, 135)), start=2):
            result = update(state, frame, [row("A", positions[0]), row("B", positions[1], appearance=1)])
            self.assertEqual([a.track_id for a in result], ids)
            self.assertEqual([a.student_id for a in result], ["A", "B"])

    def test_ambiguous_crossing_discards_identity_and_uniform_evidence(self):
        state, before = self.settled([row("A", 100), row("B", 300)])
        self.assertTrue(all(a.reliable_identity for a in before))
        result = update(state, 5, [row("A", 200), row("B", 200)])
        self.assertTrue(all(a.state == IDENTITY_UNCERTAIN for a in result))
        self.assertTrue(all(not a.student_id and not a.accepted_categories for a in result))
        self.assertTrue(all(a.track_id not in [b.track_id for b in before] for a in result))

    def test_distinct_unknowns_and_known_people_have_distinct_presence(self):
        state, result = self.settled([row(), row("", 300, appearance=1), row("", 480, appearance=2)])
        self.assertEqual(result[0].presence_id, f"person:1:{result[0].track_id}")
        self.assertEqual([a.state for a in result[1:]], [UNKNOWN_PERSON, UNKNOWN_PERSON])
        self.assertEqual(len({a.presence_id for a in result}), 3)
        after = update(state, 5, [row(), row("", 305, appearance=1), row("", 475, appearance=2)])
        self.assertEqual([a.presence_id for a in after], [a.presence_id for a in result])

    def test_presence_card_is_stable_while_identifying_but_new_after_return(self):
        state = LiveState()
        first = update(state, 1, [row()])[0]
        confirmed = update(state, 2, [row()])[0]
        self.assertEqual(first.presence_id, confirmed.presence_id)
        self.assertFalse(first.reliable_identity)
        self.assertTrue(confirmed.reliable_identity)
        update(state, 3, [], at=5.)
        returned = update(state, 4, [row()], at=6.)[0]
        self.assertNotEqual(returned.presence_id, confirmed.presence_id)
        new_session = update(state, 5, [row()], at=7., generation=2)[0]
        self.assertNotEqual(new_session.presence_id, returned.presence_id)

    def test_identity_needs_repeated_distinct_frames(self):
        state = LiveState()
        first = update(state, 1, [row()])[0]
        self.assertEqual(first.state, IDENTIFYING)
        self.assertFalse(first.reliable_identity)
        self.assertEqual(update(state, 1, [row()]), ())
        second = update(state, 2, [row()])[0]
        self.assertTrue(second.reliable_identity)
        self.assertEqual(second.state, CHECKING_UNIFORM)

    def test_uniform_needs_repeated_fresh_frames_and_supporting_snapshot(self):
        state = LiveState()
        results = []
        for frame in range(1, 5):
            results.append(update(state, frame, [row(uniform="wrong_uniform")])[0])
        self.assertFalse(any(a.accepted_categories for a in results[:3]))
        self.assertEqual(results[3].accepted_categories, ("wrong_uniform",))
        self.assertEqual(results[3].context.frame_id, ("camera", 4))
        # Retries remain possible after a failed persistence operation, but only
        # using another fresh supporting frame, not by counting the same frame.
        self.assertEqual(update(state, 4, [row(uniform="wrong_uniform")]), ())
        retry = update(state, 5, [row(uniform="wrong_uniform")])[0]
        self.assertEqual(retry.accepted_categories, ("wrong_uniform",))
        opposing = update(state, 6, [row(uniform="correct_uniform")])[0]
        self.assertEqual(opposing.state, CHECKING_UNIFORM)
        self.assertEqual(opposing.accepted_categories, ())

    def test_identity_change_and_transient_unknown_clear_history(self):
        for changed in (row("B", uniform="wrong_uniform"), row("", uniform="wrong_uniform")):
            state, old = self.settled([row(uniform="wrong_uniform")])
            result = update(state, 5, [changed])[0]
            self.assertEqual(result.state, IDENTITY_UNCERTAIN)
            self.assertFalse(result.reliable_identity)
            self.assertEqual(result.accepted_categories, ())
            recovered = update(state, 6, [row(uniform="wrong_uniform")])[0]
            self.assertFalse(recovered.reliable_identity)
            self.assertEqual(recovered.accepted_categories, ())

    def test_competing_identity_claims_never_get_next_available_student(self):
        state = LiveState()
        for frame in range(1, 6):
            result = update(state, frame, [row("A", 100), row("A", 350, appearance=1)])
            self.assertTrue(all(a.state == IDENTITY_UNCERTAIN for a in result))
            self.assertTrue(all(not a.student_id and not a.accepted_categories for a in result))

    def test_missing_or_bad_torso_and_disabled_model_never_mean_compliant(self):
        for change in (dict(association_valid=False), dict(torso_valid=False),
                       dict(uniform_available=False), dict(torso_box=None),
                       dict(uniform_label=None), dict(uniform_conf=.2)):
            state, old = self.settled()
            current = update(state, 5, [row(**change)])[0]
            self.assertEqual(current.state, UNIFORM_NOT_ASSESSED)
            self.assertIsNone(current.uniform_label)
            self.assertFalse(current.accepted_categories)

    def test_ambiguous_body_and_changed_ownership_clear_violation_history(self):
        for change in (dict(association_valid=False), dict(torso_valid=False),
                       dict(body_box=[0, 0, 630, 475])):
            state, old = self.settled([row(uniform="wrong_uniform")])
            current = update(state, 5, [row(uniform="wrong_uniform", **change)])[0]
            self.assertFalse(current.accepted_categories)
            recovered = update(state, 6, [row(uniform="wrong_uniform")])[0]
            self.assertFalse(recovered.accepted_categories)

    def test_explicit_ownership_token_change_resets_evidence(self):
        state, old = self.settled([row(uniform="wrong_uniform", association_token="owner-a")])
        current = update(state, 5, [row(uniform="wrong_uniform", association_token="owner-b")])[0]
        self.assertEqual(current.state, CHECKING_UNIFORM)
        self.assertFalse(current.accepted_categories)

    def test_evidence_expires_even_with_continuous_identity(self):
        config = LiveConfig(evidence_ttl=.8, evidence_window=5)
        state, old = self.settled([row(uniform="wrong_uniform")], config)
        self.assertTrue(old[0].accepted_categories)
        # Keep identity but provide abstentions until older uniform evidence expires.
        for frame in range(5, 10):
            update(state, frame, [row(uniform=None)])
        current = update(state, 10, [row(uniform="wrong_uniform")])[0]
        self.assertEqual(current.state, CHECKING_UNIFORM)
        self.assertFalse(current.accepted_categories)

    def test_uniform_hysteresis_needs_three_agreements_to_change(self):
        state, _ = self.settled([row(uniform="wrong_uniform")])
        for frame in (5, 6):
            result = update(state, frame, [row()])[0]
            self.assertEqual(result.state, CHECKING_UNIFORM)
            self.assertFalse(result.accepted_categories)
        self.assertEqual(update(state, 7, [row()])[0].state, UNIFORM_COMPLIANT)

    def test_delayed_old_session_out_of_order_and_future_results_rejected(self):
        state, old = self.settled()
        self.assertEqual(update(state, 5, [row()], at=1.3, now=10.), ())
        self.assertEqual(update(state, 5, [row()], at=1.3, now=1.2), ())
        self.assertEqual(update(state, 3, [row()], at=.9), ())
        result = update(state, 1, [row()], at=2., generation=2)[0]
        self.assertFalse(result.reliable_identity)
        self.assertEqual(update(state, 100, [row()], at=2.1, generation=1), ())
        self.assertTrue(update(state, 2, [row()], at=2.2, generation=2)[0].reliable_identity)

    def test_empty_frames_do_not_render_coasted_old_assessments(self):
        state, old = self.settled()
        self.assertEqual(update(state, 5, []), ())
        returned = update(state, 6, [row()])[0]
        self.assertEqual(returned.track_id, old[0].track_id)
        later = update(state, 7, [row()], at=8.)[0]
        self.assertNotEqual(later.track_id, old[0].track_id)
        self.assertFalse(later.reliable_identity)

    def test_occluded_track_recovers_id_but_requires_new_identity_and_uniform_evidence(self):
        state, old = self.settled([row(uniform="wrong_uniform"), row("B", 350, appearance=1)])
        self.assertEqual(old[0].accepted_categories, ("wrong_uniform",))
        visible = update(state, 5, [row("B", 350, appearance=1)])[0]
        self.assertEqual(visible.track_id, old[1].track_id)
        recovered = update(state, 6, [row(uniform="wrong_uniform"), row("B", 350, appearance=1)])
        self.assertEqual(recovered[0].track_id, old[0].track_id)
        self.assertFalse(recovered[0].reliable_identity)
        self.assertEqual(recovered[0].state, IDENTITY_UNCERTAIN)
        self.assertFalse(recovered[0].accepted_categories)
        self.assertTrue(recovered[1].reliable_identity)
        confirming = update(state, 7, [row(uniform="wrong_uniform")])[0]
        self.assertTrue(confirming.reliable_identity)
        self.assertEqual(confirming.state, CHECKING_UNIFORM)
        self.assertFalse(confirming.accepted_categories)

    def test_snapshot_is_immutable_and_does_not_share_input_dictionaries(self):
        state, _ = self.settled()
        source = row()
        result = update(state, 5, [source])[0]
        source["box"][0] = 999
        source["name"] = "Wrong Student"
        self.assertEqual(result.face_box[0], 100)
        self.assertEqual(result.name, "Student A")
        self.assertIsInstance(result.accepted_categories, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.name = "Wrong Student"

    def test_earrings_independent_of_torso_and_male_eligibility(self):
        state, result = self.settled([row(torso_valid=False, earring_violation=True)])
        self.assertEqual(result[0].state, UNIFORM_NOT_ASSESSED)
        self.assertEqual(result[0].accepted_categories, ("earring",))
        for changes in (dict(gender="Female"), dict(discipline_eligible=False), dict(earring_violation=None)):
            current = dict(torso_valid=False, earring_violation=True)
            current.update(changes)
            result = update(state, 5, [row(**current)])[0]
            self.assertFalse(result.accepted_categories)
            state, _ = self.settled([row(torso_valid=False, earring_violation=True)])

    def test_separate_accepted_categories_are_auditable(self):
        _, result = self.settled([row(uniform="wrong_uniform", earring_violation=True)])
        self.assertEqual(result[0].accepted_categories, ("wrong_uniform", "earring"))

    def test_suspension_metadata_only_exposed_after_reliable_identity(self):
        state = LiveState()
        detected = row(student_status="Enrolled", suspension_tag="Suspended")
        self.assertFalse(update(state, 1, [detected])[0].suspension_tag)
        current = update(state, 2, [detected])[0]
        self.assertEqual(current.suspension_tag, "Suspended")
        self.assertEqual(current.student_id, "A")


if __name__ == "__main__":
    unittest.main()
