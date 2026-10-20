"""Regression scenarios for throttling, resumptions and real checks (no network)."""
import contextlib
import datetime as dt
import email.message
import email.utils
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import checker


UTC = dt.timezone.utc
START = dt.datetime(2026, 10, 20, 15, 3, 17, tzinfo=UTC)


def jobs_page(*identifiers):
    return "".join(
        '<li data-entity-urn="urn:li:jobPosting:' + identifier + '">'
        '<h3 class="base-search-card__title">Designer ' + identifier + '</h3></li>'
        for identifier in identifiers
    )


def as_datetime(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


class CheckerScenarios(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state_path = Path(self.directory.name) / "etat.json"
        self.summary_path = Path(self.directory.name) / "summary.md"
        self.start_patch(patch.object(checker, "STATE_FILE", str(self.state_path)))
        self.start_patch(patch.dict(os.environ, {
            "COMPANY_ID": "1=Studio",
            "NTFY_TOPIC": "unit-test-never-sent",
            "INTERVALLE_CONTROLE_MINUTES": "10",
            "GITHUB_STEP_SUMMARY": str(self.summary_path),
        }, clear=True))
        # A forgotten mock must fail instead of contacting LinkedIn or ntfy.
        self.network = self.start_patch(patch.object(
            checker.urllib.request, "urlopen",
            side_effect=AssertionError("Network forbidden in tests"),
        ))
        self.notifications = self.start_patch(patch.object(checker, "notify"))
        self.start_patch(patch.object(checker.time, "sleep"))
        self.start_patch(patch.object(checker, "SEUIL_ALERTE_ECHECS", 6))

    def start_patch(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def save(self, **values):
        state = {
            "ids": [], "init": [], "post_ids": [], "posts_init": [],
            "echecs": 0, "keepalive": "2026-10",
            "derniere_verification_reussie": "",
        }
        state.update(values)
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    def state(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def run_at(self, now, response=None, *, side_effect=None, force=False):
        with patch.object(checker, "maintenant_utc", return_value=now), \
                patch.object(checker, "http_get", return_value=response,
                             side_effect=side_effect) as fetch, \
                patch.dict(os.environ, {"FORCE_CHECK": str(force).lower()}), \
                contextlib.redirect_stdout(io.StringIO()):
            result = checker.main()
        return result, fetch

    def test_429_pauses_across_runs_and_force_cannot_bypass_it(self):
        previous_success = (START - dt.timedelta(hours=2)).isoformat()
        self.save(init=["1"], ids=["100"],
                  derniere_verification_reussie=previous_success)
        result, fetch = self.run_at(START, side_effect=checker.LimitationLinkedIn())
        self.assertEqual(result, 0)
        self.assertEqual(fetch.call_count, 1)
        limited = self.state()
        self.assertEqual(limited["dernier_statut"], "limite")
        self.assertEqual(as_datetime(limited["limitation"]["reprise_apres"]),
                         START + dt.timedelta(minutes=30))
        self.assertEqual(limited["derniere_verification_reussie"], previous_success)

        result, fetch = self.run_at(START + dt.timedelta(minutes=29), force=True)
        self.assertEqual(result, 0)
        fetch.assert_not_called()
        deferred = self.state()
        self.assertEqual(deferred["dernier_statut"], "differe")
        self.assertEqual(deferred["derniere_tentative"], limited["derniere_tentative"])
        self.assertEqual(deferred["limitation"], limited["limitation"])
        self.assertEqual(deferred["derniere_verification_reussie"], previous_success)
        self.notifications.assert_not_called()

        result, fetch = self.run_at(START + dt.timedelta(minutes=30), jobs_page("100"))
        self.assertEqual(result, 0)
        fetch.assert_called_once()
        recovered = self.state()
        self.assertEqual(recovered["dernier_statut"], "succes")
        self.assertEqual(recovered["limitation"], {})
        self.assertEqual(recovered["echecs"], 0)
        self.assertEqual(as_datetime(recovered["derniere_verification_reussie"]),
                         START + dt.timedelta(minutes=30))

    def test_first_429_stops_other_companies_and_publications(self):
        with patch.dict(os.environ, {
            "COMPANY_ID": "1=One,2=Two,3=Three", "COMPANY_POSTS": "studio=Studio",
        }):
            result, fetch = self.run_at(START, side_effect=checker.LimitationLinkedIn())
        self.assertEqual(result, 0)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.state()["init"], [])
        self.assertEqual(self.state()["posts_init"], [])
        self.notifications.assert_not_called()

    def test_partial_progress_is_saved_when_a_later_company_is_limited(self):
        with patch.dict(os.environ, {
            "COMPANY_ID": "1=One,2=Two,3=Three", "COMPANY_POSTS": "studio=Studio",
        }):
            result, fetch = self.run_at(START, side_effect=[
                jobs_page("100"), checker.LimitationLinkedIn(),
            ])
        self.assertEqual(result, 0)
        self.assertEqual(fetch.call_count, 2)
        state = self.state()
        self.assertEqual(state["ids"], ["100"])
        self.assertEqual(state["init"], ["1"])
        self.assertEqual(state["derniere_verification_reussie"], "")
        self.notifications.assert_not_called()

    def test_429_from_publications_also_sets_global_pause(self):
        with patch.dict(os.environ, {"COMPANY_POSTS": "first=First,second=Second"}):
            result, fetch = self.run_at(START, side_effect=[
                jobs_page("100"), checker.LimitationLinkedIn(),
            ])
        self.assertEqual(result, 0)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(self.state()["dernier_statut"], "limite")
        self.assertEqual(self.state()["ids"], ["100"])
        self.assertEqual(self.state()["posts_init"], [])

    def test_retry_after_is_respected_without_shortening_local_backoff(self):
        cases = [
            ("7200", dt.timedelta(hours=2)),
            (email.utils.format_datetime(START + dt.timedelta(hours=3), usegmt=True),
             dt.timedelta(hours=3)),
            ("60", dt.timedelta(minutes=30)),
            ("not-a-date", dt.timedelta(minutes=30)),
            (email.utils.format_datetime(START - dt.timedelta(hours=1), usegmt=True),
             dt.timedelta(minutes=30)),
        ]
        for header, expected_pause in cases:
            with self.subTest(header=header):
                self.save()
                result, _ = self.run_at(START,
                    side_effect=checker.LimitationLinkedIn(retry_after=header))
                self.assertEqual(result, 0)
                self.assertEqual(as_datetime(self.state()["limitation"]["reprise_apres"]),
                                 START + expected_pause)

    def test_backoff_grows_and_persistent_alert_is_sent_only_once(self):
        now = START
        for attempt, delay in enumerate([30, 60, 120, 240, 360, 360, 360], 1):
            with self.subTest(attempt=attempt):
                result, fetch = self.run_at(now, side_effect=checker.LimitationLinkedIn())
                self.assertEqual(result, 1 if attempt == 6 else 0)
                fetch.assert_called_once()
                state = self.state()
                self.assertEqual(state["limitation"]["tentatives"], attempt)
                self.assertEqual(state["derniere_verification_reussie"], "")
                next_attempt = as_datetime(state["limitation"]["reprise_apres"])
                self.assertEqual(next_attempt - now, dt.timedelta(minutes=delay))
                now = next_attempt
        self.notifications.assert_called_once()
        self.assertTrue(self.state()["limitation"]["alerte_envoyee"])

    def test_24_hour_limitation_alerts_even_below_attempt_threshold(self):
        self.save(limitation={
            "depuis": (START - dt.timedelta(hours=25)).isoformat(),
            "derniere": (START - dt.timedelta(hours=1)).isoformat(),
            "reprise_apres": START.isoformat(),
            "tentatives": 1, "alerte_envoyee": False,
        })
        result, _ = self.run_at(START, side_effect=checker.LimitationLinkedIn())
        self.assertEqual(result, 1)
        self.notifications.assert_called_once()
        self.assertTrue(self.state()["limitation"]["alerte_envoyee"])

    def test_long_server_pause_alerts_after_24_hours_without_retrying_linkedin(self):
        result, _ = self.run_at(START,
            side_effect=checker.LimitationLinkedIn(retry_after="172800"))
        self.assertEqual(result, 0)
        result, fetch = self.run_at(START + dt.timedelta(hours=24), force=True)
        self.assertEqual(result, 1)
        fetch.assert_not_called()
        self.notifications.assert_called_once()
        self.assertEqual(self.state()["limitation"]["tentatives"], 1)
        self.assertTrue(self.state()["limitation"]["alerte_envoyee"])
        result, fetch = self.run_at(START + dt.timedelta(hours=25), force=True)
        self.assertEqual(result, 0)
        fetch.assert_not_called()
        self.notifications.assert_called_once()

    def test_summary_identifies_incomplete_check_and_keeps_previous_success(self):
        previous = (START - dt.timedelta(hours=2)).isoformat()
        self.save(derniere_verification_reussie=previous)
        self.run_at(START, side_effect=checker.LimitationLinkedIn())
        summary = self.summary_path.read_text(encoding="utf-8")
        self.assertIn("429", summary)
        self.assertIn(previous, summary)
        self.assertIn(self.state()["limitation"]["reprise_apres"], summary)
        self.assertNotIn("**Contrôle effectué**", summary)

    def test_elapsed_cadence_does_not_depend_on_wall_clock_minute(self):
        first_result, first_fetch = self.run_at(START, jobs_page("100"))
        self.assertEqual(first_result, 0)
        first_fetch.assert_called_once()
        result, fetch = self.run_at(START + dt.timedelta(minutes=9, seconds=59))
        self.assertEqual(result, 0)
        fetch.assert_not_called()
        self.assertEqual(as_datetime(self.state()["derniere_verification_reussie"]), START)
        later = START + dt.timedelta(minutes=10, seconds=7)
        result, fetch = self.run_at(later, jobs_page("100"))
        self.assertEqual(result, 0)
        fetch.assert_called_once()
        self.assertEqual(as_datetime(self.state()["derniere_verification_reussie"]), later)

    def test_force_can_bypass_normal_cadence(self):
        self.run_at(START, jobs_page("100"))
        later = START + dt.timedelta(minutes=1)
        result, fetch = self.run_at(later, jobs_page("100"), force=True)
        self.assertEqual(result, 0)
        fetch.assert_called_once()
        self.assertEqual(as_datetime(self.state()["derniere_verification_reussie"]), later)

    def test_recovery_only_notifies_new_offers_once(self):
        self.run_at(START, jobs_page("100"))
        self.notifications.assert_not_called()
        self.run_at(START + dt.timedelta(minutes=10),
                    side_effect=checker.LimitationLinkedIn())
        self.notifications.assert_not_called()
        recovery = START + dt.timedelta(minutes=40)
        result, _ = self.run_at(recovery, jobs_page("100", "101"))
        self.assertEqual(result, 0)
        self.notifications.assert_called_once()
        self.assertEqual(self.state()["ids"], ["100", "101"])
        result, _ = self.run_at(recovery + dt.timedelta(minutes=10), jobs_page("100", "101"))
        self.assertEqual(result, 0)
        self.notifications.assert_called_once()

    def test_server_error_remains_a_real_failure_and_preserves_last_success(self):
        previous = (START - dt.timedelta(hours=1)).isoformat()
        self.save(derniere_verification_reussie=previous)
        result, fetch = self.run_at(START, side_effect=urllib.error.HTTPError(
            "https://www.linkedin.com/test", 500, "Server error", {}, None,
        ))
        self.assertEqual(result, 1)
        fetch.assert_called_once()
        self.assertEqual(self.state()["dernier_statut"], "echec")
        self.assertEqual(self.state()["derniere_verification_reussie"], previous)
        self.assertEqual(self.state()["echecs"], 1)

    def test_later_429_does_not_hide_an_earlier_company_server_error(self):
        previous = (START - dt.timedelta(hours=1)).isoformat()
        self.save(derniere_verification_reussie=previous)
        with patch.dict(os.environ, {"COMPANY_ID": "1=One,2=Two,3=Three"}):
            result, fetch = self.run_at(START, side_effect=[
                urllib.error.HTTPError(
                    "https://www.linkedin.com/test", 500, "Server error", {}, None,
                ),
                checker.LimitationLinkedIn(),
            ])
        self.assertEqual(result, 1)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(self.state()["derniere_verification_reussie"], previous)
        self.assertEqual(as_datetime(self.state()["limitation"]["reprise_apres"]),
                         START + dt.timedelta(minutes=30))
        self.assertEqual(self.state()["echecs"], 1)

    def test_notification_failure_retries_hourly_without_repeating_github_failure(self):
        self.save(echecs=5, limitation={
            "depuis": (START - dt.timedelta(hours=5)).isoformat(),
            "derniere": (START - dt.timedelta(hours=4)).isoformat(),
            "reprise_apres": START.isoformat(),
            "tentatives": 5, "alerte_envoyee": False,
        })
        self.notifications.side_effect = OSError("ntfy temporarily unavailable")
        result, _ = self.run_at(START, side_effect=checker.LimitationLinkedIn())
        self.assertEqual(result, 1)
        self.assertEqual(self.notifications.call_count, 1)
        state = self.state()
        self.assertTrue(state["limitation"]["alerte_github_signalee"])
        self.assertFalse(state["limitation"]["alerte_envoyee"])
        next_check = as_datetime(state["limitation"]["reprise_apres"])

        result, fetch = self.run_at(START + dt.timedelta(minutes=59), force=True)
        self.assertEqual(result, 0)
        fetch.assert_not_called()
        self.assertEqual(self.notifications.call_count, 1)

        result, fetch = self.run_at(START + dt.timedelta(hours=1), force=True)
        self.assertEqual(result, 0)
        fetch.assert_not_called()
        self.assertEqual(self.notifications.call_count, 2)
        self.assertFalse(self.state()["limitation"]["alerte_envoyee"])

        # Another genuine 429 must preserve the already-issued GitHub alert,
        # even though delivery of the ntfy alert has still not succeeded.
        result, fetch = self.run_at(next_check, side_effect=checker.LimitationLinkedIn())
        self.assertEqual(result, 0)
        fetch.assert_called_once()
        self.assertEqual(self.notifications.call_count, 3)
        self.assertTrue(self.state()["limitation"]["alerte_github_signalee"])
        self.assertFalse(self.state()["limitation"]["alerte_envoyee"])

        self.notifications.side_effect = None
        result, fetch = self.run_at(next_check + dt.timedelta(hours=1))
        self.assertEqual(result, 0)
        fetch.assert_not_called()
        self.assertEqual(self.notifications.call_count, 4)
        self.assertTrue(self.state()["limitation"]["alerte_envoyee"])
        result, fetch = self.run_at(next_check + dt.timedelta(hours=2))
        self.assertEqual(result, 0)
        fetch.assert_not_called()
        self.assertEqual(self.notifications.call_count, 4)

    def test_summary_uses_new_cadence_when_previous_429_pause_has_expired(self):
        expired = START - dt.timedelta(minutes=5)
        self.save(limitation={
            "depuis": (START - dt.timedelta(hours=1)).isoformat(),
            "derniere": (START - dt.timedelta(minutes=35)).isoformat(),
            "reprise_apres": expired.isoformat(),
            "tentatives": 1, "alerte_envoyee": False,
        })
        result, _ = self.run_at(START, side_effect=urllib.error.HTTPError(
            "https://www.linkedin.com/test", 500, "Server error", {}, None,
        ))
        self.assertEqual(result, 1)
        expected = self.state()["prochain_controle"]
        summary = self.summary_path.read_text(encoding="utf-8")
        next_attempt_line = next(line for line in summary.splitlines()
                                 if line.startswith("- Prochaine tentative"))
        self.assertIn(expected, next_attempt_line)
        self.assertNotIn(expired.isoformat(), next_attempt_line)

    def test_suspicious_html_is_not_recorded_as_success(self):
        result, _ = self.run_at(START, "<html>Please sign in</html>")
        self.assertEqual(result, 1)
        self.assertEqual(self.state()["dernier_statut"], "echec")
        self.assertEqual(self.state()["derniere_verification_reussie"], "")
        self.assertEqual(self.state()["init"], [])

    def test_legitimate_empty_job_list_does_not_drop_known_ids(self):
        self.save(ids=["100"], init=["1"])
        result, _ = self.run_at(START, "<!DOCTYPE html>\n<!---->")
        self.assertEqual(result, 0)
        self.assertEqual(self.state()["ids"], ["100"])
        self.assertEqual(self.state()["dernier_statut"], "succes")
        self.notifications.assert_not_called()

    def test_http_layer_translates_429_but_not_other_http_errors(self):
        headers = email.message.Message()
        headers["Retry-After"] = "7200"
        error = urllib.error.HTTPError(
            "https://www.linkedin.com/test", 429, "Too Many Requests", headers, None,
        )
        self.network.side_effect = error
        with self.assertRaises(checker.LimitationLinkedIn) as caught:
            checker.http_get("https://www.linkedin.com/test")
        self.save()
        result, _ = self.run_at(START, side_effect=caught.exception)
        self.assertEqual(result, 0)
        self.assertEqual(as_datetime(self.state()["limitation"]["reprise_apres"]),
                         START + dt.timedelta(hours=2))

        self.network.side_effect = urllib.error.HTTPError(
            "https://www.linkedin.com/test", 500, "Server error", {}, None,
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            checker.http_get("https://www.linkedin.com/test")
        self.assertEqual(caught.exception.code, 500)


if __name__ == "__main__":
    unittest.main()
