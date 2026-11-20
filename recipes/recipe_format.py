import json


PREFIX = "SMARTCHEF_JSON_V1"
MAX_RESPONSE_CHARS = 30000
EXPECTED_KEYS = {"title", "ingredients", "steps"}


class RecipeFormatError(ValueError):
    """A recipe response or stored recipe does not match the supported format."""


def _object_without_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecipeFormatError("JSON құрамында қайталанған кілт бар.")
        result[key] = value
    return result


def _reject_json_constant(value):
    raise RecipeFormatError(f"JSON ішінде рұқсат етілмеген мән бар: {value}.")


def _nonempty_string(value, field, maximum):
    if not isinstance(value, str):
        raise RecipeFormatError(f"{field} мәтін болуы керек.")
    normalized = value.strip()
    if not normalized:
        raise RecipeFormatError(f"{field} бос болмауы керек.")
    if len(normalized) > maximum:
        raise RecipeFormatError(f"{field} ұзындығы {maximum} таңбадан аспауы керек.")
    return normalized


def validate_recipe_object(value):
    if not isinstance(value, dict):
        raise RecipeFormatError("Рецепт JSON нысаны болуы керек.")
    if set(value.keys()) != EXPECTED_KEYS:
        raise RecipeFormatError("JSON тек title, ingredients және steps өрістерін қамтуы керек.")

    title = _nonempty_string(value["title"], "title", 120)

    ingredients = value["ingredients"]
    if not isinstance(ingredients, list) or not 1 <= len(ingredients) <= 24:
        raise RecipeFormatError("ingredients ішінде 1–24 өнім жолы болуы керек.")
    ingredients = [
        _nonempty_string(item, "ingredients жолы", 200)
        for item in ingredients
    ]

    steps = value["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= 12:
        raise RecipeFormatError("steps ішінде 1–12 қадам болуы керек.")
    steps = [_nonempty_string(item, "steps жолы", 1200) for item in steps]

    return {"title": title, "ingredients": ingredients, "steps": steps}


def parse_recipe_response(response_text):
    if not isinstance(response_text, str) or not response_text.strip():
        raise RecipeFormatError("Gemini жауабы бос болды.")
    if len(response_text) > MAX_RESPONSE_CHARS:
        raise RecipeFormatError("Gemini жауабы 30000 таңбадан ұзын.")
    try:
        value = json.loads(
            response_text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except RecipeFormatError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RecipeFormatError("Gemini жауабы дұрыс JSON емес.") from exc
    return validate_recipe_object(value)


def encode_recipe(value):
    payload = validate_recipe_object(value)
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    stored = f"{PREFIX}\n{serialized}"
    if len(stored) > MAX_RESPONSE_CHARS:
        raise RecipeFormatError("Тексерілген рецепт 30000 таңбадан ұзын.")
    return stored


def decode_recipe(stored_text):
    if not isinstance(stored_text, str):
        raise RecipeFormatError("Сақталған рецепт мәтін болуы керек.")
    if not stored_text.startswith(PREFIX):
        return None
    marker = f"{PREFIX}\n"
    if not stored_text.startswith(marker):
        raise RecipeFormatError("SMARTCHEF_JSON_V1 белгісі қате.")
    return parse_recipe_response(stored_text[len(marker):])
