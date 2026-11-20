import json
import os
import uuid

from django.conf import settings
from django.http import HttpResponseNotFound
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors

from .catalog import CATEGORIES, PRODUCTS, build_products
from .models import SavedRecipe
from .recipe_format import RecipeFormatError, decode_recipe, encode_recipe, parse_recipe_response


load_dotenv(settings.BASE_DIR / ".env", override=True)
COOKING_SESSION_KEY = "smartchef_cooking"


def _language(value):
    return "ru" if value == "ru" else "kk"


def _valid_selected_ids(raw_ids):
    valid_ids = {product["id"] for product in PRODUCTS}
    return list(dict.fromkeys(product_id for product_id in raw_ids if product_id in valid_ids))


def _base_context(language, **values):
    return {
        "language": language,
        "products": "",
        "combined_products": "",
        "minutes": 30,
        "vegetarian": False,
        "recipe": "",
        "recipe_payload": None,
        "recipe_token": "",
        "cooking_url": "",
        "is_demo": False,
        "error": "",
        "can_save": False,
        "categories": CATEGORIES,
        "catalog_products": PRODUCTS,
        "selected_products": [],
        "catalog_data": {"categories": CATEGORIES, "products": PRODUCTS, "language": language},
        **values,
    }


def _demo_payload(combined, language):
    ingredients = [part.strip() for part in combined.split(",") if part.strip()][:24]
    if language == "ru":
        return {
            "title": "Учебный демо-рецепт SmartChef",
            "ingredients": ingredients,
            "steps": [
                "Подготовьте выбранные продукты и нарежьте их небольшими кусочками.",
                "Готовьте продукты вместе на среднем огне до мягкости, безопасной готовности и появления аромата.",
                "Проверьте готовность, подавайте блюдо горячим.",
            ],
        }
    return {
        "title": "SmartChef оқу демо-рецепті",
        "ingredients": ingredients,
        "steps": [
            "Таңдалған өнімдерді дайындап, қажет болса ұсақтап тураңыз.",
            "Өнімдерді орташа отта жұмсарғанша және толық піскенше бірге дайындаңыз.",
            "Дайындығын тексеріп, тағамды ыстық күйінде ұсыныңыз.",
        ],
    }


def _restore_draft_context(request, context):
    draft = request.session.get("smartchef_draft")
    if not isinstance(draft, dict) or _language(draft.get("language")) != context["language"]:
        return
    stored_recipe = draft.get("recipe")
    if not isinstance(stored_recipe, str) or not stored_recipe:
        return

    context["recipe"] = stored_recipe
    context["combined_products"] = draft.get("products", "")
    context["products"] = draft.get("form_products", "")
    context["selected_products"] = _valid_selected_ids(draft.get("selected_products", []))
    context["minutes"] = draft.get("minutes", 30)
    context["vegetarian"] = bool(draft.get("vegetarian", False))
    context["recipe_token"] = str(draft.get("token", ""))
    context["is_demo"] = bool(draft.get("demo", False))
    context["can_save"] = bool(context["recipe_token"])
    try:
        context["recipe_payload"] = decode_recipe(stored_recipe)
    except RecipeFormatError:
        context["recipe_payload"] = None
        context["error"] = (
            "Сақталған рецепт пішімін оқу мүмкін болмады."
            if context["language"] == "kk"
            else "Не удалось прочитать формат сохранённого рецепта."
        )
        context["can_save"] = False
    if context["recipe_payload"] and context["recipe_token"]:
        context["cooking_url"] = reverse("recipes:cooking", args=[context["recipe_token"]])


def index(request):
    language = _language(
        request.POST.get("language", "kk")
        if request.method == "POST"
        else request.GET.get("language", "kk")
    )
    context = _base_context(language)

    if request.method == "GET":
        _restore_draft_context(request, context)
        return render(request, "recipes/index.html", context)

    action = request.POST.get("action", "generate")
    raw_ids = request.POST.getlist("selected_products")
    context["selected_products"] = _valid_selected_ids(raw_ids)
    context["products"] = request.POST.get("products", "").strip()
    context["vegetarian"] = request.POST.get("vegetarian") == "on"
    try:
        context["minutes"] = int(request.POST.get("minutes", "30"))
    except (TypeError, ValueError):
        context["minutes"] = 30

    if action == "language":
        request.session.pop("smartchef_draft", None)
        request.session.pop(COOKING_SESSION_KEY, None)
        return render(request, "recipes/index.html", context)

    if action != "generate":
        return render(request, "recipes/index.html", context)

    request.session.pop("smartchef_draft", None)
    request.session.pop(COOKING_SESSION_KEY, None)
    try:
        minutes = int(request.POST.get("minutes", "30"))
        if minutes not in (15, 30, 60):
            raise ValueError
        context["minutes"] = minutes
    except (TypeError, ValueError):
        context["error"] = (
            "Уақытты 15, 30 немесе 60 минут деп таңдаңыз."
            if language == "kk"
            else "Выберите время: 15, 30 или 60 минут."
        )
        return render(request, "recipes/index.html", context)

    try:
        selected_ids, combined = build_products(raw_ids, context["products"], language)
    except ValueError as exc:
        context["error"] = str(exc)
        return render(request, "recipes/index.html", context)

    demo_mode = os.getenv("SMARTCHEF_DEMO", "").strip() == "1"
    try:
        if demo_mode:
            payload = parse_recipe_response(json.dumps(_demo_payload(combined, language), ensure_ascii=False))
        else:
            api_key = os.getenv("GEMINI_API_KEY", "").strip()
            if not api_key or api_key == "PASTE_YOUR_KEY_HERE":
                context["error"] = (
                    ".env файлындағы GEMINI_API_KEY мәнін тексеріңіз."
                    if language == "kk"
                    else "Проверьте GEMINI_API_KEY в файле .env."
                )
                return render(request, "recipes/index.html", context)

            model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
            if language == "ru":
                prompt = (
                    "Верни только один JSON-объект без Markdown и пояснений. "
                    "Точная схема: {\"title\": строка, \"ingredients\": массив из 1–24 строк, "
                    "\"steps\": массив из 1–12 строк}. title до 120 символов, ингредиент до 200, "
                    "шаг до 1200 символов. Не добавляй другие ключи. Продукты: "
                    f"{combined}. Время приготовления: не более {minutes} минут."
                )
                if context["vegetarian"]:
                    prompt += " Рецепт должен быть без мяса, птицы и рыбы."
            else:
                prompt = (
                    "Тек JSON нысанын ғана қайтар: Markdown және түсіндірме болмасын. "
                    "Нақты құрылым: {\"title\": мәтін, \"ingredients\": 1–24 мәтіннен тұратын массив, "
                    "\"steps\": 1–12 мәтіннен тұратын массив}. title 120 таңбаға дейін, әр өнім 200, "
                    "әр қадам 1200 таңбаға дейін. Басқа кілт қоспа. Өнімдер: "
                    f"{combined}. Дайындау уақыты {minutes} минуттан аспасын."
                )
                if context["vegetarian"]:
                    prompt += " Тағам етсіз болсын: ет, құс еті және балық қоспа."

            with genai.Client(api_key=api_key) as client:
                response = client.models.generate_content(model=model, contents=prompt)
            payload = parse_recipe_response(response.text or "")

        stored_recipe = encode_recipe(payload)
    except RecipeFormatError as exc:
        context["error"] = (
            f"Рецепт құрылымы тексеруден өтпеді: {exc}"
            if language == "kk"
            else f"Структура рецепта не прошла проверку: {exc}"
        )
        return render(request, "recipes/index.html", context)
    except genai_errors.APIError as exc:
        if getattr(exc, "code", None) == 429 or getattr(exc, "status", "") == "RESOURCE_EXHAUSTED":
            context["error"] = (
                "Gemini сұрау лимиті таусылды. Кейінірек қайталап көріңіз."
                if language == "kk"
                else "Лимит Gemini исчерпан. Попробуйте позже."
            )
        else:
            context["error"] = (
                "Gemini сұрауы орындалмады. API кілті мен модельді тексеріңіз."
                if language == "kk"
                else "Запрос Gemini не выполнен. Проверьте API-ключ и модель."
            )
        return render(request, "recipes/index.html", context)
    except Exception:
        context["error"] = (
            "Gemini-ден жауап алынбады. Интернет пен API баптауын тексеріңіз."
            if language == "kk"
            else "Не удалось получить ответ Gemini. Проверьте интернет и настройки API."
        )
        return render(request, "recipes/index.html", context)

    token = str(uuid.uuid4())
    context.update(
        recipe=stored_recipe,
        recipe_payload=payload,
        combined_products=combined,
        recipe_token=token,
        cooking_url=reverse("recipes:cooking", args=[token]),
        can_save=True,
        is_demo=demo_mode,
    )
    request.session["smartchef_draft"] = {
        "token": token,
        "products": combined,
        "recipe": stored_recipe,
        "language": language,
        "minutes": int(minutes),
        "vegetarian": bool(context["vegetarian"]),
        "form_products": context["products"],
        "selected_products": selected_ids,
        "demo": demo_mode,
    }
    return render(request, "recipes/index.html", context)


@require_POST
def save_recipe(request):
    draft = request.session.get("smartchef_draft")
    if not isinstance(draft, dict) or not draft.get("token"):
        return redirect("recipes:index")
    SavedRecipe.objects.get_or_create(
        token=draft["token"],
        defaults={
            "products": draft["products"],
            "recipe": draft["recipe"],
            "language": _language(draft.get("language", "kk")),
            "minutes": int(draft.get("minutes", 30)),
            "vegetarian": bool(draft.get("vegetarian", False)),
        },
    )
    return redirect(f"/saved/?language={_language(draft.get('language', 'kk'))}")


def saved_recipes(request):
    language = _language(request.GET.get("language", "kk"))
    entries = []
    for item in SavedRecipe.objects.all():
        try:
            payload = decode_recipe(item.recipe)
            format_error = False
        except RecipeFormatError:
            payload = None
            format_error = True
        entries.append({"item": item, "recipe_payload": payload, "format_error": format_error})
    return render(
        request,
        "recipes/saved.html",
        {"language": language, "saved_recipes": entries},
    )


def _new_cooking_progress(token):
    return {
        "token": str(token),
        "open_step": 0,
        "completed_steps": [],
        "finished": False,
    }


def _cooking_source(request, token):
    draft = request.session.get("smartchef_draft")
    if isinstance(draft, dict) and draft.get("token") == str(token):
        return {
            "recipe": draft.get("recipe", ""),
            "language": _language(draft.get("language", "kk")),
            "is_draft": True,
            "item": None,
        }
    item = SavedRecipe.objects.filter(token=token).first()
    if item is None:
        return None
    return {
        "recipe": item.recipe,
        "language": _language(item.language),
        "is_draft": False,
        "item": item,
    }


@require_http_methods(["GET", "POST"])
def cooking(request, token):
    source = _cooking_source(request, token)
    if source is None:
        return HttpResponseNotFound("Рецепт табылмады.")

    language = _language(
        request.POST.get("language", source["language"])
        if request.method == "POST"
        else request.GET.get("language", source["language"])
    )
    try:
        payload = decode_recipe(source["recipe"])
    except RecipeFormatError:
        payload = None

    if payload is None:
        return render(
            request,
            "recipes/cooking.html",
            {
                "language": language,
                "unavailable": True,
                "legacy_text": source["recipe"],
                "back_url": reverse("recipes:index") if source["is_draft"] else reverse("recipes:saved_recipes"),
            },
            status=422,
        )

    if request.method == "GET":
        progress = _new_cooking_progress(token)
        request.session[COOKING_SESSION_KEY] = progress
    else:
        action = request.POST.get("action", "")
        if action == "exit":
            request.session.pop(COOKING_SESSION_KEY, None)
            target = reverse("recipes:index") if source["is_draft"] else reverse("recipes:saved_recipes")
            return redirect(f"{target}?language={language}")

        progress = request.session.get(COOKING_SESSION_KEY)
        if not isinstance(progress, dict) or progress.get("token") != str(token):
            progress = _new_cooking_progress(token)

        step_count = len(payload["steps"])
        try:
            open_step = int(progress.get("open_step", 0))
        except (TypeError, ValueError):
            open_step = 0
        open_step = max(0, min(open_step, step_count - 1))
        completed = progress.get("completed_steps", [])
        if not isinstance(completed, list):
            completed = []
        completed = sorted({
            value for value in completed
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < step_count
        })
        finished = bool(progress.get("finished", False)) and len(completed) == step_count

        if action == "restart":
            progress = _new_cooking_progress(token)
        elif action == "back" and not finished:
            progress = {
                "token": str(token),
                "open_step": max(0, open_step - 1),
                "completed_steps": completed,
                "finished": False,
            }
        elif action == "confirm" and not finished:
            try:
                submitted_step = int(request.POST.get("step", "-1"))
            except (TypeError, ValueError):
                submitted_step = -1
            if submitted_step == open_step:
                if open_step not in completed:
                    completed.append(open_step)
                    completed.sort()
                is_last_step = open_step == step_count - 1
                progress = {
                    "token": str(token),
                    "open_step": open_step if is_last_step else open_step + 1,
                    "completed_steps": completed,
                    "finished": is_last_step,
                }
            else:
                progress = {
                    "token": str(token),
                    "open_step": open_step,
                    "completed_steps": completed,
                    "finished": finished,
                }
        else:
            progress = {
                "token": str(token),
                "open_step": open_step,
                "completed_steps": completed,
                "finished": finished,
            }
        request.session[COOKING_SESSION_KEY] = progress

    open_step = progress["open_step"]
    completed_steps = progress["completed_steps"]
    finished = progress["finished"]
    step_count = len(payload["steps"])
    return render(
        request,
        "recipes/cooking.html",
        {
            "language": language,
            "unavailable": False,
            "recipe": payload,
            "recipe_token": str(token),
            "is_draft": source["is_draft"],
            "step": None if finished else payload["steps"][open_step],
            "step_number": open_step + 1,
            "step_index": open_step,
            "step_count": step_count,
            "completed_count": len(completed_steps),
            "progress_percent": round(len(completed_steps) * 100 / step_count),
            "finished": finished,
            "back_disabled": open_step == 0 or finished,
        },
    )
