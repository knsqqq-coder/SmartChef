import json
import os
import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse

from .models import SavedRecipe
from .recipe_format import (
    PREFIX,
    RecipeFormatError,
    decode_recipe,
    encode_recipe,
    parse_recipe_response,
)


def valid_payload(steps=None):
    return {
        "title": "Жұмыртқалы тағам",
        "ingredients": ["2 жұмыртқа", "Ірімшік"],
        "steps": steps or ["Өнімдерді дайындаңыз.", "Қуырып пісіріңіз."],
    }


def stored_recipe(payload=None):
    return encode_recipe(payload or valid_payload())


class RecipeFormatTests(TestCase):
    def test_valid_json_round_trips_with_version_prefix(self):
        payload = parse_recipe_response(json.dumps(valid_payload(), ensure_ascii=False))
        encoded = encode_recipe(payload)
        self.assertTrue(encoded.startswith(PREFIX + "\n"))
        self.assertEqual(decode_recipe(encoded), payload)

    def test_legacy_text_is_not_split_into_steps(self):
        self.assertIsNone(decode_recipe("Ескі рецепт. Бір мәтін күйінде қалады."))

    def test_rejects_invalid_json_duplicate_keys_and_extra_keys(self):
        invalid = [
            "{",
            '{"title":"A","title":"B","ingredients":["x"],"steps":["y"]}',
            json.dumps({**valid_payload(), "unexpected": True}),
            json.dumps({**valid_payload(), "steps": []}),
            json.dumps({**valid_payload(), "ingredients": [" "] }),
        ]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(RecipeFormatError):
                parse_recipe_response(raw)

    def test_rejects_type_and_field_length_errors(self):
        cases = [
            {"title": 1, "ingredients": ["x"], "steps": ["y"]},
            {"title": "x" * 121, "ingredients": ["x"], "steps": ["y"]},
            {"title": "x", "ingredients": ["x" * 201], "steps": ["y"]},
            {"title": "x", "ingredients": ["x"], "steps": ["y" * 1201]},
            {"title": "x", "ingredients": ["x"] * 25, "steps": ["y"]},
            {"title": "x", "ingredients": ["x"], "steps": ["y"] * 13},
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(RecipeFormatError):
                encode_recipe(value)

    def test_rejects_response_over_limit(self):
        with self.assertRaises(RecipeFormatError):
            parse_recipe_response(" " * 30001)


class CookingModeTests(TestCase):
    def setUp(self):
        self.demo_env = patch.dict(os.environ, {"SMARTCHEF_DEMO": "1"})
        self.demo_env.start()
        self.addCleanup(self.demo_env.stop)

    def generate_demo(self, steps=None):
        if steps is not None:
            payload = valid_payload(steps)
            self.demo_env.stop()
            self.demo_env = patch.dict(os.environ, {"SMARTCHEF_DEMO": "0"})
            self.demo_env.start()
            self.addCleanup(self.demo_env.stop)
            fake_client = MagicMock()
            fake_client.__enter__.return_value.models.generate_content.return_value.text = json.dumps(
                payload, ensure_ascii=False
            )
            provider_patch = patch("recipes.views.genai.Client", return_value=fake_client)
            provider_patch.start()
            self.addCleanup(provider_patch.stop)
        return self.client.post(
            reverse("recipes:index"),
            {
                "action": "generate",
                "language": "kk",
                "selected_products": ["egg", "cheese", "milk"],
                "products": "Аскөк",
                "minutes": "15",
            },
        )

    def put_draft(self, payload, language="kk"):
        token = uuid.uuid4()
        request_session = self.client.session
        request_session["smartchef_draft"] = {
            "token": str(token),
            "products": "Өнімдер",
            "recipe": stored_recipe(payload),
            "language": language,
            "minutes": 30,
            "vegetarian": False,
            "form_products": "",
            "selected_products": ["egg"],
        }
        request_session.save()
        return token

    def test_t1_demo_recipe_is_valid_saved_reopened_and_cookable(self):
        response = self.generate_demo()
        self.assertEqual(response.status_code, 200)
        draft = self.client.session["smartchef_draft"]
        self.assertTrue(response.context["is_demo"])
        payload = decode_recipe(draft["recipe"])
        self.assertEqual(payload["title"], "SmartChef оқу демо-рецепті")
        self.assertIn("Аскөк", draft["products"])

        save_response = self.client.post(reverse("recipes:save_recipe"))
        self.assertEqual(save_response.status_code, 302)
        self.assertEqual(SavedRecipe.objects.count(), 1)
        self.assertTrue(self.client.session.get("smartchef_draft"))

        saved_response = self.client.get(reverse("recipes:saved_recipes"))
        self.assertContains(saved_response, "SmartChef оқу демо-рецепті")
        self.assertContains(saved_response, "Аскөк")

        cooking_response = self.client.get(
            reverse("recipes:cooking", args=[draft["token"]])
        )
        self.assertEqual(cooking_response.status_code, 200)
        self.assertEqual(cooking_response.context["step_number"], 1)
        self.assertEqual(cooking_response.context["completed_count"], 0)

    def test_t2_invalid_provider_responses_never_make_a_draft_or_saved_row(self):
        invalid_responses = [
            "",
            "not json",
            json.dumps({"title": "x", "ingredients": ["x"], "steps": []}),
            '{"title":"a","title":"b","ingredients":["x"],"steps":["y"]}',
            json.dumps({**valid_payload(), "extra": "not allowed"}),
            "x" * 30001,
        ]
        for raw in invalid_responses:
            with self.subTest(raw_length=len(raw)):
                self.client.session.pop("smartchef_draft", None)
                self.client.session.save()
                fake_client = MagicMock()
                fake_client.__enter__.return_value.models.generate_content.return_value.text = raw
                with patch.dict(os.environ, {"SMARTCHEF_DEMO": "0"}), patch(
                    "recipes.views.genai.Client", return_value=fake_client
                ):
                    response = self.client.post(
                        reverse("recipes:index"),
                        {
                            "action": "generate",
                            "language": "kk",
                            "selected_products": ["egg"],
                            "products": "",
                            "minutes": "30",
                        },
                    )
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context["can_save"])
                self.assertFalse(self.client.session.get("smartchef_draft"))
                self.assertEqual(SavedRecipe.objects.count(), 0)

    def test_provider_failure_and_empty_products_are_rejected_without_draft(self):
        with patch("recipes.views.genai.Client") as provider:
            empty = self.client.post(
                reverse("recipes:index"),
                {"action": "generate", "language": "kk", "products": "", "minutes": "30"},
            )
            self.assertEqual(empty.status_code, 200)
            self.assertContains(empty, "Өнімдерді таңда")
            provider.assert_not_called()

        fake_client = MagicMock()
        fake_client.__enter__.return_value.models.generate_content.side_effect = RuntimeError("offline")
        with patch.dict(os.environ, {"SMARTCHEF_DEMO": "0"}), patch(
            "recipes.views.genai.Client", return_value=fake_client
        ):
            response = self.client.post(
                reverse("recipes:index"),
                {
                    "action": "generate",
                    "language": "kk",
                    "selected_products": ["egg"],
                    "products": "",
                    "minutes": "30",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_save"])
        self.assertFalse(self.client.session.get("smartchef_draft"))
        self.assertEqual(SavedRecipe.objects.count(), 0)

    def test_t3_legacy_saved_text_and_draft_are_preserved_without_cooking_steps(self):
        token = uuid.uuid4()
        old_text = "Бұрынғы рецепт мәтіні. Қадамдарға бөлінбейді."
        item = SavedRecipe.objects.create(
            token=token,
            products="Картоп",
            recipe=old_text,
            language="kk",
            minutes=30,
            vegetarian=False,
        )
        saved = self.client.get(reverse("recipes:saved_recipes"))
        self.assertContains(saved, old_text)
        self.assertNotContains(saved, "1-қадам")

        unavailable = self.client.get(reverse("recipes:cooking", args=[token]))
        self.assertEqual(unavailable.status_code, 422)
        self.assertContains(unavailable, "қолжетімсіз", status_code=422)
        self.assertContains(unavailable, old_text, status_code=422)

        session = self.client.session
        session["smartchef_draft"] = {
            "token": str(uuid.uuid4()),
            "products": "Ескі өнімдер",
            "recipe": old_text,
            "language": "kk",
            "minutes": 30,
            "vegetarian": False,
        }
        session.save()
        home = self.client.get(reverse("recipes:index"))
        self.assertContains(home, old_text)
        self.assertTrue(home.context["can_save"])
        self.assertIsNone(home.context["recipe_payload"])

    def test_t4_one_step_finishes_and_duplicate_confirmation_counts_once(self):
        token = self.put_draft(valid_payload(["Тек бір қадамды орындаңыз."]))
        url = reverse("recipes:cooking", args=[token])
        first = self.client.get(url)
        self.assertEqual(first.context["step_number"], 1)
        self.assertTrue(first.context["back_disabled"])

        finished = self.client.post(url, {"action": "confirm", "step": "0", "language": "kk"})
        self.assertTrue(finished.context["finished"])
        self.assertEqual(finished.context["completed_count"], 1)

        repeated = self.client.post(url, {"action": "confirm", "step": "0", "language": "kk"})
        self.assertTrue(repeated.context["finished"])
        self.assertEqual(repeated.context["completed_count"], 1)

    def test_t5_back_reconfirm_and_stale_double_submit_do_not_double_count(self):
        token = self.put_draft(valid_payload(["Бірінші қадам.", "Екінші қадам.", "Үшінші қадам."]))
        url = reverse("recipes:cooking", args=[token])
        self.client.get(url)

        step_two = self.client.post(url, {"action": "confirm", "step": "0", "language": "kk"})
        self.assertEqual(step_two.context["step_number"], 2)
        self.assertEqual(step_two.context["completed_count"], 1)

        duplicate = self.client.post(url, {"action": "confirm", "step": "0", "language": "kk"})
        self.assertEqual(duplicate.context["step_number"], 2)
        self.assertEqual(duplicate.context["completed_count"], 1)

        back = self.client.post(url, {"action": "back", "step": "1", "language": "kk"})
        self.assertEqual(back.context["step_number"], 1)
        self.assertEqual(back.context["completed_count"], 1)

        reconfirm = self.client.post(url, {"action": "confirm", "step": "0", "language": "kk"})
        self.assertEqual(reconfirm.context["step_number"], 2)
        self.assertEqual(reconfirm.context["completed_count"], 1)

        step_three = self.client.post(url, {"action": "confirm", "step": "1", "language": "kk"})
        self.assertEqual(step_three.context["step_number"], 3)
        self.assertEqual(step_three.context["completed_count"], 2)

        finished = self.client.post(url, {"action": "confirm", "step": "2", "language": "kk"})
        self.assertTrue(finished.context["finished"])
        self.assertEqual(finished.context["completed_count"], 3)

    def test_t6_refresh_or_other_recipe_starts_fresh_and_exit_keeps_draft(self):
        first_token = self.put_draft(valid_payload(["Бірінші.", "Екінші."]))
        first_url = reverse("recipes:cooking", args=[first_token])
        self.client.get(first_url)
        progressed = self.client.post(first_url, {"action": "confirm", "step": "0", "language": "kk"})
        self.assertEqual(progressed.context["completed_count"], 1)

        refreshed = self.client.get(first_url)
        self.assertEqual(refreshed.context["step_number"], 1)
        self.assertEqual(refreshed.context["completed_count"], 0)

        other = SavedRecipe.objects.create(
            products="Күріш",
            recipe=stored_recipe(valid_payload(["Басқа рецепттің қадамы."])),
            language="kk",
            minutes=30,
            vegetarian=False,
        )
        other_page = self.client.get(reverse("recipes:cooking", args=[other.token]))
        self.assertEqual(other_page.context["step_number"], 1)
        self.assertEqual(other_page.context["completed_count"], 0)

        exit_response = self.client.post(
            first_url, {"action": "exit", "language": "kk"}
        )
        self.assertEqual(exit_response.status_code, 302)
        self.assertEqual(self.client.session["smartchef_draft"]["token"], str(first_token))
        home = self.client.get(reverse("recipes:index"))
        self.assertContains(home, "Жұмыртқалы тағам")
        self.assertTrue(home.context["can_save"])

    def test_t7_ru_kz_text_is_escaped_and_layout_is_responsive(self):
        payload = {
            "title": "<script>alert(1)</script>",
            "ingredients": ["<img src=x onerror=alert(1)>"],
            "steps": ["<script>step()</script>"],
        }
        token = self.put_draft(payload)
        response = self.client.get(
            reverse("recipes:cooking", args=[token]) + "?language=ru"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>")
        self.assertContains(response, "lang=\"ru\"")

    def test_t8_lab5_catalog_and_saving_remain_available(self):
        home = self.client.get(reverse("recipes:index"))
        self.assertEqual(home.status_code, 200)
        self.assertEqual(home.content.decode().count('data-product-id="'), 24)
        self.assertContains(home, "catalog-search")
        self.assertContains(home, "catalog.js")
        response = self.generate_demo()
        self.assertTrue(response.context["can_save"])
        draft = self.client.session["smartchef_draft"]
        with patch("recipes.views.genai.Client") as provider:
            self.client.post(reverse("recipes:save_recipe"))
            provider.assert_not_called()
        self.assertTrue(SavedRecipe.objects.filter(token=draft["token"]).exists())
        self.assertEqual(SavedRecipe.objects.count(), 1)


class RecipeSuggestionTests(TestCase):
    def setUp(self):
        self.original_text = stored_recipe()
        self.recipe = SavedRecipe.objects.create(
            products="Жұмыртқа, Ірімшік",
            recipe=self.original_text,
            language="kk",
            minutes=30,
            vegetarian=False,
        )
        self.url = reverse("recipes:suggest_recipe", args=[self.recipe.token])
        self.post_data = {
            "language": "kk",
            "change_request": "Сүт қоспа",
        }

    def post_live_response(self, response_text):
        fake_client = MagicMock()
        fake_client.__enter__.return_value.models.generate_content.return_value.text = response_text
        with patch.dict(
            os.environ,
            {
                "SMARTCHEF_DEMO": "0",
                "GEMINI_API_KEY": "test-key-not-real",
                "GEMINI_MODEL": "gemini-3.8-flash",
            },
        ), patch("recipes.views.genai.Client", return_value=fake_client):
            response = self.client.post(self.url, self.post_data)
        return response, fake_client

    def test_invalid_json_or_schema_never_changes_saved_recipe(self):
        invalid_responses = [
            "",
            "not json",
            '{"title":"a","title":"b","ingredients":["x"],"steps":["y"]}',
            json.dumps({**valid_payload(), "extra": True}),
            json.dumps({**valid_payload(), "steps": []}),
            json.dumps({**valid_payload(), "title": []}),
        ]
        for raw in invalid_responses:
            with self.subTest(raw=raw):
                self.post_live_response(raw)
                self.recipe.refresh_from_db()
                self.assertEqual(self.recipe.recipe, self.original_text)
                pending = self.client.session["smartchef_recipe_editor"]
                self.assertEqual(pending["recipe_id"], self.recipe.pk)
                self.assertEqual(pending["original_text"], self.original_text)
                self.assertEqual(pending["candidate"], "")
                self.assertTrue(pending["pending_token"])
                self.assertGreater(pending["expires_at"], 0)

    def test_provider_error_and_timeout_leave_original_unchanged(self):
        for failure in (RuntimeError("offline"), TimeoutError("request timed out")):
            with self.subTest(failure=type(failure).__name__):
                fake_client = MagicMock()
                fake_client.__enter__.return_value.models.generate_content.side_effect = failure
                with patch.dict(
                    os.environ,
                    {
                        "SMARTCHEF_DEMO": "0",
                        "GEMINI_API_KEY": "test-key-not-real",
                        "GEMINI_MODEL": "gemini-3.8-flash",
                    },
                ), patch("recipes.views.genai.Client", return_value=fake_client):
                    self.client.post(self.url, self.post_data)
                self.recipe.refresh_from_db()
                self.assertEqual(self.recipe.recipe, self.original_text)
                state = self.client.session["smartchef_recipe_editor"]
                self.assertEqual(state["candidate"], "")
                self.assertEqual(state["error_code"], "timeout" if isinstance(failure, TimeoutError) else "provider")

    def test_pending_proposal_is_server_session_data_then_accept_and_undo(self):
        candidate = valid_payload(["Жұмыртқаны бөлек араластырыңыз."])
        response, fake_client = self.post_live_response(json.dumps(candidate, ensure_ascii=False))
        self.assertEqual(response.status_code, 302)
        fake_client.__enter__.return_value.models.generate_content.assert_called_once()

        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.recipe, self.original_text)
        pending = self.client.session["smartchef_recipe_editor"]
        self.assertEqual(pending["recipe_id"], self.recipe.pk)
        self.assertEqual(pending["original_text"], self.original_text)
        self.assertEqual(decode_recipe(pending["candidate"]), candidate)
        self.assertEqual(len(pending["pending_token"]), 36)

        comparison = self.client.get(reverse("recipes:saved_recipes"))
        self.assertContains(comparison, "ТҮПНҰСҚА")
        self.assertContains(comparison, candidate["title"])
        self.assertContains(comparison, "Қабылдау")
        self.assertContains(comparison, "Бас тарту")

        accepted = self.client.post(
            reverse("recipes:accept_suggestion", args=[self.recipe.token]),
            {"pending_token": pending["pending_token"], "language": "kk"},
        )
        self.assertEqual(accepted.status_code, 302)
        self.recipe.refresh_from_db()
        accepted_text = self.recipe.recipe
        self.assertEqual(decode_recipe(accepted_text), candidate)
        undo = self.client.session["smartchef_recipe_editor_undo"]
        self.assertEqual(undo["original_text"], self.original_text)

        # A repeated accept has no pending session token and cannot overwrite the row.
        repeated = self.client.post(
            reverse("recipes:accept_suggestion", args=[self.recipe.token]),
            {"pending_token": pending["pending_token"], "language": "kk"},
        )
        self.assertEqual(repeated.status_code, 302)
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.recipe, accepted_text)

        undone = self.client.post(
            reverse("recipes:undo_suggestion", args=[self.recipe.token]),
            {"undo_token": undo["token"], "language": "kk"},
        )
        self.assertEqual(undone.status_code, 302)
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.recipe, self.original_text)

        # The snapshot is consumed: a second POST cannot apply it again.
        second_undo = self.client.post(
            reverse("recipes:undo_suggestion", args=[self.recipe.token]),
            {"undo_token": undo["token"], "language": "kk"},
        )
        self.assertEqual(second_undo.status_code, 302)
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.recipe, self.original_text)

    def test_undo_does_not_overwrite_a_newer_recipe(self):
        candidate = valid_payload(["Қабылданған қадам."])
        self.post_live_response(json.dumps(candidate, ensure_ascii=False))
        pending = self.client.session["smartchef_recipe_editor"]
        self.client.post(
            reverse("recipes:accept_suggestion", args=[self.recipe.token]),
            {"pending_token": pending["pending_token"], "language": "kk"},
        )
        undo = self.client.session["smartchef_recipe_editor_undo"]

        newer_text = stored_recipe(valid_payload(["Жаңарақ рецепт."]))
        SavedRecipe.objects.filter(pk=self.recipe.pk).update(recipe=newer_text)
        response = self.client.post(
            reverse("recipes:undo_suggestion", args=[self.recipe.token]),
            {"undo_token": undo["token"], "language": "kk"},
        )
        self.assertEqual(response.status_code, 302)
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.recipe, newer_text)
        self.assertNotIn("smartchef_recipe_editor_undo", self.client.session)

    def test_stale_proposal_cannot_overwrite_recipe_changed_since_suggestion(self):
        candidate = valid_payload(["Ескірген ұсыныстың қадамы."])
        self.post_live_response(json.dumps(candidate, ensure_ascii=False))
        pending = self.client.session["smartchef_recipe_editor"]

        newer_text = stored_recipe(valid_payload(["Басқа жерде жаңартылды."]))
        SavedRecipe.objects.filter(pk=self.recipe.pk).update(recipe=newer_text)
        response = self.client.post(
            reverse("recipes:accept_suggestion", args=[self.recipe.token]),
            {"pending_token": pending["pending_token"], "language": "kk"},
        )
        self.assertEqual(response.status_code, 302)
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.recipe, newer_text)

    def test_reject_does_not_change_saved_recipe_and_demo_is_not_live(self):
        demo_original = stored_recipe(
            {
                "title": "Пештегі көкөніс омлеті",
                "ingredients": ["Жұмыртқа", "Ірімшік", "Сүт", "Аскөк"],
                "steps": ["Қоспаны дайындаңыз.", "Пеште пісіріңіз."],
            }
        )
        demo_recipe = SavedRecipe.objects.create(
            products="Жұмыртқа, Ірімшік, Сүт, Аскөк",
            recipe=demo_original,
            language="kk",
            minutes=30,
            vegetarian=False,
        )
        demo_url = reverse("recipes:suggest_recipe", args=[demo_recipe.token])
        demo_request = {
            "language": "kk",
            "change_request": "Пештің орнына табада дайындауға бейімде",
        }
        with patch.dict(os.environ, {"SMARTCHEF_DEMO": "1"}), patch(
            "recipes.views.genai.Client"
        ) as provider:
            self.client.post(demo_url, demo_request)
            provider.assert_not_called()
        pending = self.client.session["smartchef_recipe_editor"]
        self.assertTrue(pending["demo"])
        comparison = self.client.get(reverse("recipes:saved_recipes"))
        self.assertContains(comparison, "Демо, Gemini емес")
        self.assertContains(comparison, "Табадағы көкөніс омлеті")
        demo_recipe.refresh_from_db()
        self.assertEqual(demo_recipe.recipe, demo_original)

        response = self.client.post(
            reverse("recipes:reject_suggestion", args=[demo_recipe.token]),
            {"pending_token": pending["pending_token"], "language": "kk"},
        )
        self.assertEqual(response.status_code, 302)
        demo_recipe.refresh_from_db()
        self.assertEqual(demo_recipe.recipe, demo_original)
        self.assertNotIn("smartchef_recipe_editor", self.client.session)
