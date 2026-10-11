import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import bot


class CarpetUIEnhancementsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous_db_path = bot.DB_PATH
        self.previous_database_url = bot.DATABASE_URL
        self.temp_dir = tempfile.TemporaryDirectory()
        bot.DB_PATH = Path(self.temp_dir.name) / "test_ui.sqlite3"
        bot.DATABASE_URL = ""

    def tearDown(self):
        bot.DB_PATH = self.previous_db_path
        bot.DATABASE_URL = self.previous_database_url
        self.temp_dir.cleanup()

    def test_parse_inventory_edit_input_supports_all_formats(self):
        # 1. Traditional pipes
        res1 = bot.parse_inventory_edit_input("Katta Qizil Gul | 10 | dona")
        self.assertEqual(res1, {"name": "Katta Qizil Gul", "quantity": 10.0, "unit": "dona"})

        res2 = bot.parse_inventory_edit_input("Sag | 7.5 | metr")
        self.assertEqual(res2, {"name": "Sag", "quantity": 7.5, "unit": "metr"})

        res3 = bot.parse_inventory_edit_input("Elen | 4")
        self.assertEqual(res3, {"name": "Elen", "quantity": 4.0, "unit": "dona"})

        # 2. Natural language
        res4 = bot.parse_inventory_edit_input("Katta Qizil Gul 10 dona")
        self.assertEqual(res4, {"name": "Katta Qizil Gul", "quantity": 10.0, "unit": "dona"})

        res5 = bot.parse_inventory_edit_input("Sag 7 metr")
        self.assertEqual(res5, {"name": "Sag", "quantity": 7.0, "unit": "metr"})

        res6 = bot.parse_inventory_edit_input("Navro'z 5 ta")
        self.assertEqual(res6, {"name": "Navro'z", "quantity": 5.0, "unit": "dona"})

        res7 = bot.parse_inventory_edit_input("Afshon 12.5 m")
        self.assertEqual(res7, {"name": "Afshon", "quantity": 12.5, "unit": "metr"})

        res8 = bot.parse_inventory_edit_input("Model 55 8")
        self.assertEqual(res8, {"name": "Model 55", "quantity": 8.0, "unit": "dona"})

        # 3. Comma and dash formats
        res9 = bot.parse_inventory_edit_input("Katta Qizil Gul, 10, dona")
        self.assertEqual(res9, {"name": "Katta Qizil Gul", "quantity": 10.0, "unit": "dona"})

        res10 = bot.parse_inventory_edit_input("Katta Qizil Gul - 10 dona")
        self.assertEqual(res10, {"name": "Katta Qizil Gul", "quantity": 10.0, "unit": "dona"})

        # 4. Input with prices
        res11 = bot.parse_inventory_edit_input("Elen 5 dona kelish 200000 sotish 300000")
        self.assertEqual(
            res11,
            {
                "name": "Elen",
                "quantity": 5.0,
                "unit": "dona",
                "purchase_price": 200000.0,
                "sale_price": 300000.0,
            },
        )

    def test_parse_bulk_inventory_input_supports_natural_lines(self):
        text = "Sag 7 metr\nKatta Qizil Gul 10 dona\nElen 5 ta\nModel | 3 | dona"
        items, error = bot.parse_bulk_inventory_input(text)
        self.assertIsNone(error)
        self.assertEqual(len(items), 4)
        self.assertEqual(items[0], {"name": "Sag", "quantity": 7.0, "unit": "metr"})
        self.assertEqual(items[1], {"name": "Katta Qizil Gul", "quantity": 10.0, "unit": "dona"})
        self.assertEqual(items[2], {"name": "Elen", "quantity": 5.0, "unit": "dona"})
        self.assertEqual(items[3], {"name": "Model", "quantity": 3.0, "unit": "dona"})

    async def test_step_by_step_wizard_callback_flow(self):
        # 1. Start wizard
        query = SimpleNamespace(data="wiz:start", answer=AsyncMock(), edit_message_text=AsyncMock())
        context = SimpleNamespace(user_data={})
        await bot.wizard_callback(SimpleNamespace(callback_query=query), context)

        self.assertEqual(context.user_data["wizard_step"], "name")
        self.assertIn("Gilam nomini kiriting", query.edit_message_text.await_args.args[0])

        # 2. Enter name via handle_text
        message = SimpleNamespace(text="Afshon Classic", reply_text=AsyncMock())
        await bot.handle_text(SimpleNamespace(message=message), context)
        self.assertEqual(context.user_data["wizard_data"]["name"], "Afshon Classic")
        self.assertEqual(context.user_data["wizard_step"], "quantity")

        # 3. Select quantity via wizard button
        query_qty = SimpleNamespace(data="wiz:qty:5", answer=AsyncMock(), edit_message_text=AsyncMock())
        await bot.wizard_callback(SimpleNamespace(callback_query=query_qty), context)
        self.assertEqual(context.user_data["wizard_data"]["quantity"], 5.0)
        self.assertEqual(context.user_data["wizard_step"], "unit")

        # 4. Select unit via wizard button -> stages inventory
        query_unit = SimpleNamespace(data="wiz:unit:metr", answer=AsyncMock(), edit_message_text=AsyncMock())
        await bot.wizard_callback(
            SimpleNamespace(callback_query=query_unit, effective_message=query_unit),
            context,
        )
        self.assertIn("pending_inventory", context.user_data)
        pending = context.user_data["pending_inventory"]
        self.assertEqual(pending["name"], "Afshon Classic")
        self.assertEqual(pending["quantity"], 5.0)
        self.assertEqual(pending["unit"], "metr")

    async def test_pending_preview_quick_quantity_adjustment(self):
        pending = {
            "name": "Elen",
            "quantity": 3.0,
            "unit": "dona",
            "purchase_price": None,
            "sale_price": None,
        }
        context = SimpleNamespace(user_data={"pending_inventory": pending})
        query = SimpleNamespace(data="inv:qty:5", answer=AsyncMock(), edit_message_text=AsyncMock())

        await bot.inventory_callback(SimpleNamespace(callback_query=query), context)

        self.assertEqual(context.user_data["pending_inventory"]["quantity"], 8.0)
        preview_text = query.edit_message_text.await_args.args[0]
        self.assertIn("8 dona", preview_text)

    async def test_entering_two_dollar_prices_saves_both_prices(self):
        bot.set_usd_rate(12_850)
        pending = {
            "name": "Katta Qizil Gul",
            "quantity": 1,
            "unit": "dona",
            "area_sqm": 20,
            "purchase_price": None,
            "sale_price": None,
        }
        context = SimpleNamespace(
            user_data={
                "pending_inventory": pending,
                "entering_price_for_pending": True,
            }
        )
        message = SimpleNamespace(text="30$ 40$", reply_text=AsyncMock())

        await bot.handle_text(SimpleNamespace(message=message), context)

        self.assertNotIn("entering_price_for_pending", context.user_data)
        self.assertEqual(pending["purchase_price_usd"], 30)
        self.assertEqual(pending["purchase_price"], 385_500)
        self.assertEqual(pending["sale_price"], 514_000)
        preview_text = message.reply_text.await_args.args[0]
        self.assertIn("Kelish narxi: $30.00", preview_text)
        self.assertIn("Sotish narxi: 514 000", preview_text)

    async def test_invalid_price_entry_keeps_price_prompt_active(self):
        pending = {
            "name": "Elen",
            "quantity": 1,
            "unit": "dona",
            "purchase_price": None,
            "sale_price": None,
        }
        context = SimpleNamespace(
            user_data={
                "pending_inventory": pending,
                "entering_price_for_pending": True,
            }
        )
        message = SimpleNamespace(text="narx noma'lum", reply_text=AsyncMock())

        await bot.handle_text(SimpleNamespace(message=message), context)

        self.assertTrue(context.user_data["entering_price_for_pending"])
        self.assertIn("Qayta yuboring", message.reply_text.await_args.args[0])

    async def test_add_rejects_explicit_unit_mismatch_for_existing_product(self):
        bot.inventory_create("Metr gilam", "metr", 5, None, None)
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_message=message)
        context = SimpleNamespace(user_data={})

        await bot.stage_inventory_add(
            update,
            context,
            {"name": "Metr gilam", "quantity": 2, "unit": "dona"},
            "Metr gilam 2 dona keldi",
        )

        message.reply_text.assert_awaited_once()
        self.assertIn("omborda metr bilan", message.reply_text.await_args.args[0])
        self.assertNotIn("pending_inventory", context.user_data)
        product = bot.inventory_get_by_key(bot.inventory_key_for("Metr gilam"))
        self.assertEqual(product["quantity"], 5)

    async def test_add_without_unit_uses_existing_inventory_unit(self):
        bot.inventory_create("Metr gilam", "metr", 5, None, None)
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_message=message)
        context = SimpleNamespace(user_data={})

        await bot.stage_inventory_add(
            update,
            context,
            {"name": "Metr gilam", "quantity": 2, "unit": "dona"},
            "Metr gilam 2 keldi",
        )

        self.assertEqual(context.user_data["pending_inventory"]["unit"], "metr")

    async def test_single_name_input_in_add_mode_prompts_for_quantity(self):
        context = SimpleNamespace(user_data={"adding_inventory": True})
        message = SimpleNamespace(text="Katta Qizil Gul", reply_text=AsyncMock())

        await bot.handle_text(SimpleNamespace(message=message), context)

        self.assertEqual(context.user_data["wizard_data"]["name"], "Katta Qizil Gul")
        self.assertEqual(context.user_data["wizard_step"], "quantity")
        prompt = message.reply_text.await_args.args[0]
        self.assertIn("Miqdorini tanlang", prompt)

    def test_parse_carpet_dimensions_and_dollar_pricing(self):
        # 1. User voice case 1: "Sagdan 3ga 4lik kelishi kvadrati 3 dollar"
        res1 = bot.parse_command("Sagdan 3ga 4lik kelishi kvadrati 3 dollar")
        self.assertIsNone(res1["action"])
        self.assertEqual(res1["name"], "Sag 3x4")
        self.assertEqual(res1["dimensions"], "3x4")
        self.assertEqual(res1["area_sqm"], 12.0)
        self.assertEqual(res1["price_per_sqm_usd"], 3.0)
        self.assertEqual(res1["purchase_price_usd"], 36.0)
        self.assertEqual(res1["quantity"], 1.0)
        # 36.0 * 12850 = 462,600
        self.assertEqual(res1["purchase_price"], 462600.0)

        # 2. User voice case 2: "Sag 3ga 4lik gilam kelish narxi 3 dollar"
        res2 = bot.parse_command("Sag 3ga 4lik gilam kelish narxi 3 dollar")
        self.assertIsNone(res2["action"])
        self.assertEqual(res2["name"], "Sag 3x4")
        self.assertEqual(res2["dimensions"], "3x4")
        self.assertEqual(res2["area_sqm"], 12.0)
        self.assertEqual(res2["price_per_sqm_usd"], 3.0)
        self.assertEqual(res2["purchase_price_usd"], 36.0)

        # 3. Dimensions with 'lik': "7x4 lik gilam keldi kelishi kvadrati 4 dollar soni 5 ta"
        res3 = bot.parse_command("7x4 lik gilam keldi kelishi kvadrati 4 dollar soni 5 ta")
        self.assertEqual(res3["action"], "add")
        self.assertEqual(res3["dimensions"], "7x4")
        self.assertEqual(res3["area_sqm"], 28.0)
        self.assertEqual(res3["price_per_sqm_usd"], 4.0)
        self.assertEqual(res3["purchase_price_usd"], 112.0)
        self.assertEqual(res3["quantity"], 5.0)

    def test_voice_phrase_normalizes_dimension_and_square_meter_purchase_price(self):
        bot.set_usd_rate(12_850)

        parsed = bot.parse_command(
            "Katta Qizil Gul 3 ga 4 5 dona keldi "
            "kelish narxi 3 dollardan kvadratiga"
        )

        self.assertEqual(parsed["name"], "Katta Qizil Gul 3x4")
        self.assertEqual(parsed["dimensions"], "3x4")
        self.assertEqual(parsed["area_sqm"], 12.0)
        self.assertEqual(parsed["quantity"], 5.0)
        self.assertEqual(parsed["unit"], "dona")
        self.assertEqual(parsed["price_per_sqm_usd"], 3.0)
        self.assertEqual(parsed["purchase_price_usd"], 36.0)
        self.assertEqual(parsed["purchase_price"], 462_600.0)

    def test_ai_voice_parser_uses_structured_json_and_recalculates_prices(self):
        bot.set_usd_rate(12_850)
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(
                    return_value=SimpleNamespace(
                        text=json.dumps(
                            {
                                "name": "Katta Qizil Gul",
                                "dimensions": "3x4",
                                "width": 3,
                                "length": 4,
                                "area_sqm": 999,
                                "quantity": 5,
                                "unit": "dona",
                                "price_per_sqm_usd": 3,
                                "purchase_price_usd": 1,
                                "purchase_price": None,
                                "sale_price_usd": None,
                                "sale_price": None,
                                "action": "unknown",
                            }
                        )
                    )
                )
            )
        )
        with patch.object(bot, "inventory_list_full", return_value=[]), patch.object(
            bot, "gemini_clients", [client]
        ):
            parsed = bot.parse_command_with_ai(
                "Katta Qizil Gul 3 ga 4 5 dona kelish narxi kvadratiga 3 dollar"
            )

        self.assertEqual(parsed["name"], "Katta Qizil Gul 3x4")
        self.assertEqual(parsed["dimensions"], "3x4")
        self.assertEqual(parsed["area_sqm"], 12.0)
        self.assertEqual(parsed["quantity"], 5.0)
        self.assertIsNone(parsed["action"])
        self.assertEqual(parsed["price_per_sqm_usd"], 3.0)
        self.assertEqual(parsed["purchase_price_usd"], 36.0)
        self.assertEqual(parsed["purchase_price"], 462_600.0)
        request = client.models.generate_content.call_args
        self.assertEqual(
            request.kwargs["config"].response_mime_type,
            "application/json",
        )
        self.assertIsNotNone(request.kwargs["config"].response_schema)

    def test_ai_voice_parser_rejects_malformed_json_for_legacy_fallback(self):
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(
                    return_value=SimpleNamespace(text="not JSON")
                )
            )
        )
        with patch.object(bot, "inventory_list_full", return_value=[]), patch.object(
            bot, "gemini_clients", [client]
        ):
            parsed = bot.parse_command_with_ai("Prado 3x4")

        self.assertIsNone(parsed)

    def test_ai_voice_parser_falls_back_on_gemini_transport_error(self):
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(
                    side_effect=ConnectionError("connection reset")
                )
            )
        )
        with patch.object(bot, "inventory_list_full", return_value=[]), patch.object(
            bot, "gemini_clients", [client]
        ):
            parsed = bot.parse_command_with_ai("Prado 3x4")

        self.assertIsNone(parsed)

    def test_ai_voice_parser_falls_back_when_model_not_found(self):
        def fake_generate_content(*args, **kwargs):
            model = kwargs.get("model")
            if model == "gemini-1.5-pro":
                raise RuntimeError("404 NOT_FOUND models/gemini-1.5-pro is not found")
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "name": "Prado",
                        "dimensions": "3x4",
                        "quantity": 2,
                        "unit": "dona",
                        "action": "add",
                    }
                )
            )

        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(side_effect=fake_generate_content)
            )
        )
        with patch.object(bot, "inventory_list_full", return_value=[]), patch.object(
            bot, "gemini_clients", [client]
        ), patch.object(bot, "GEMINI_MODEL", "gemini-1.5-pro"):
            parsed = bot.parse_command_with_ai("Prado 3x4 2 dona keldi")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["name"], "Prado 3x4")
        self.assertEqual(parsed["quantity"], 2.0)

    def test_explicit_quantity_detection_does_not_treat_dimensions_as_stock(self):
        self.assertTrue(
            bot.has_explicit_inventory_quantity(
                "Prado 3 ga 4 5 dona kvadratiga 3 dollar"
            )
        )
        self.assertTrue(bot.has_explicit_inventory_quantity("Prado uch dona"))
        self.assertFalse(bot.has_explicit_inventory_quantity("Prado 3 ga 4"))

    def test_actionless_voice_preview_offers_actions_with_computed_prices(self):
        bot.set_usd_rate(12_850)
        parsed = bot.parse_command(
            "Katta Qizil Gul 3x4 5 dona kelish narxi kvadratiga 3 dollar"
        )
        data = {
            "parsed": parsed,
            "text": "Katta Qizil Gul 3x4 5 dona kelish narxi kvadratiga 3 dollar",
            "name": parsed["name"],
            "quantity": parsed["quantity"],
            "unit": parsed["unit"],
            "quantity_is_explicit": True,
        }

        preview, keyboard = bot.actionless_voice_preview(data)

        self.assertIn("Gilam: Katta Qizil Gul", preview)
        self.assertIn("O‘lchami: 3x4 (12 m²)", preview)
        self.assertIn("Miqdori: 5 dona", preview)
        self.assertIn("Kvadrat narxi: $3.00/m²", preview)
        self.assertIn("Kelish narxi: $36.00 (462 600 so‘m)", preview)
        self.assertEqual(
            [
                button.callback_data
                for row in keyboard.inline_keyboard
                for button in row
            ],
            ["vact:add", "vact:sell", "vact:cancel"],
        )

    async def test_actionless_voice_add_button_stages_inventory_add(self):
        bot.set_usd_rate(12_850)
        parsed = bot.parse_command(
            "Prado 3x4 5 dona kelish narxi kvadratiga 3 dollar"
        )
        data = {
            "parsed": parsed,
            "text": "Prado 3x4 5 dona kelish narxi kvadratiga 3 dollar",
            "name": parsed["name"],
            "quantity": parsed["quantity"],
            "unit": parsed["unit"],
            "quantity_is_explicit": True,
        }
        message = SimpleNamespace(reply_text=AsyncMock())
        query = SimpleNamespace(
            data="vact:add",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            message=message,
        )
        context = SimpleNamespace(user_data={"actionless_voice_data": data})
        update = SimpleNamespace(
            callback_query=query,
            effective_message=message,
        )

        await bot.voice_action_callback(update, context)

        self.assertNotIn("actionless_voice_data", context.user_data)
        self.assertEqual(
            bot.inventory_get_with_prices(
                bot.inventory_get_by_key(
                    bot.inventory_key_for("Prado 3x4")
                )["id"]
            )["purchase_price"],
            462_600.0,
        )
        result = query.edit_message_text.await_args.args[0]
        self.assertIn("Omborga saqlandi", result)
        self.assertIn("Jami qoldiq: 5 dona", result)

    async def test_voice_without_action_shows_action_buttons_instead_of_failing(self):
        voice_file = SimpleNamespace(
            file_id="voice-file",
            file_unique_id="unique-voice-file",
        )
        message = SimpleNamespace(
            voice=voice_file,
            reply_text=AsyncMock(),
        )
        telegram_file = SimpleNamespace(download_to_drive=AsyncMock())
        context = SimpleNamespace(
            bot=SimpleNamespace(
                get_file=AsyncMock(return_value=telegram_file),
            ),
            user_data={},
        )
        update = SimpleNamespace(message=message)
        recognized = (
            "Prado 3x4 5 dona kelish narxi kvadratiga 3 dollar"
        )
        ai_parsed = {
            "action": None,
            "quantity": 5.0,
            "unit": "dona",
            "name": "Prado 3x4",
            "dimensions": "3x4",
            "area_sqm": 12.0,
            "price_per_sqm_usd": 3.0,
            "purchase_price_usd": 36.0,
            "purchase_price": 462_600.0,
            "sale_price_usd": None,
            "sale_price": None,
            "raw_text": recognized,
        }
        with patch.dict("os.environ", {"TEMP": str(self.temp_dir.name)}), patch.object(
            bot.asyncio,
            "to_thread",
            new=AsyncMock(side_effect=[recognized, ai_parsed]),
        ) as to_thread_mock, patch.object(
            bot,
            "correct_voice_transcription",
            return_value=(recognized, []),
        ):
            await bot.handle_voice(update, context)

        self.assertEqual(to_thread_mock.await_count, 2)
        self.assertIs(
            to_thread_mock.await_args_list[1].args[0],
            bot.parse_command_with_ai,
        )
        self.assertEqual(
            to_thread_mock.await_args_list[1].args[1],
            recognized,
        )
        self.assertIn("actionless_voice_data", context.user_data)
        self.assertEqual(message.reply_text.await_count, 2)
        preview_call = message.reply_text.await_args
        self.assertIn("Kelish narxi: $36.00", preview_call.args[0])
        self.assertEqual(
            [
                button.callback_data
                for row in preview_call.kwargs["reply_markup"].inline_keyboard
                for button in row
            ],
            ["vact:add", "vact:sell", "vact:cancel"],
        )

    def test_voice_action_callback_is_registered_with_admin_guard(self):
        application = SimpleNamespace(
            add_handler=Mock(),
            add_error_handler=Mock(),
        )
        builder = Mock()
        builder.token.return_value = builder
        builder.persistence.return_value = builder
        builder.post_init.return_value = builder
        builder.build.return_value = application
        with patch.object(bot.Application, "builder", return_value=builder), patch.object(
            bot,
            "BOT_TOKEN",
            "123456:local-test-token",
        ), patch.object(bot, "TELEGRAM_ADMIN_ID", "910471611"), patch.object(
            bot,
            "GEMINI_API_KEYS",
            ["local-test-key"],
        ), patch.object(bot, "gemini_clients", [object()]), patch.object(
            bot,
            "db",
            return_value=SimpleNamespace(close=Mock()),
        ):
            bot.build_application()

        handlers = [
            call.args[0]
            for call in application.add_handler.call_args_list
        ]
        voice_action_handlers = [
            handler
            for handler in handlers
            if getattr(handler, "pattern", None)
            and handler.pattern.match("vact:add")
        ]
        self.assertEqual(len(voice_action_handlers), 1)
        self.assertEqual(
            voice_action_handlers[0].callback.__name__,
            "guarded_callback",
        )

    async def test_actionless_voice_sell_button_sells_only_after_selection(self):
        bot.inventory_create("Prado 3x4", "dona", 5, 462_600, 600_000)
        parsed = bot.parse_command("Prado 3x4 2 dona")
        data = {
            "parsed": parsed,
            "text": "Prado 3x4 2 dona",
            "name": "Prado 3x4",
            "quantity": 2.0,
            "unit": "dona",
            "quantity_is_explicit": True,
        }
        query = SimpleNamespace(
            data="vact:sell",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={"actionless_voice_data": data})
        update = SimpleNamespace(callback_query=query)

        self.assertEqual(bot.inventory_get_with_prices(1)["quantity"], 5.0)
        await bot.voice_action_callback(update, context)

        self.assertEqual(bot.inventory_get_with_prices(1)["quantity"], 3.0)
        self.assertIn("Bazadan ayirildi", query.edit_message_text.await_args.args[0])
        self.assertNotIn("actionless_voice_data", context.user_data)

    async def test_actionless_voice_rejects_sale_without_explicit_quantity(self):
        data = {
            "parsed": bot.parse_command("Prado 3x4"),
            "text": "Prado 3x4",
            "name": "Prado 3x4",
            "quantity": 1.0,
            "unit": "dona",
            "quantity_is_explicit": False,
        }
        query = SimpleNamespace(
            data="vact:sell",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={"actionless_voice_data": data})

        await bot.voice_action_callback(
            SimpleNamespace(callback_query=query),
            context,
        )

        bot.inventory_create("Prado 3x4", "dona", 5, None, None)
        self.assertEqual(bot.inventory_get_with_prices(1)["quantity"], 5.0)
        self.assertIn("Miqdor aniq emas", query.edit_message_text.await_args.args[0])

    def test_gemini_prompt_normalizes_voice_into_a_parser_ready_command(self):
        audio_path = Path(self.temp_dir.name) / "sample.ogg"
        audio_path.write_bytes(b"test audio")
        generate_content = Mock(
            return_value=SimpleNamespace(
                text=(
                    "Katta Qizil Gul 3x4 5 dona keldi "
                    "kelish narxi kvadratiga 3 dollar"
                )
            )
        )
        client = SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content)
        )

        with patch.object(bot, "gemini_clients", [client]), patch.object(
            bot,
            "inventory_list_full",
            return_value=[{"name": "Katta Qizil Gul"}],
        ):
            normalized = bot.gemini_transcribe(audio_path)

        prompt = generate_content.call_args.kwargs["contents"][0]
        self.assertIn("3 dollardan kvadratiga", prompt)
        self.assertIn("Katta Qizil Gul 3x4 5 dona keldi", prompt)
        self.assertIn("unless the speaker", prompt)
        self.assertIn(
            'Normalized: "Prado 3x4 5 dona kelish narxi kvadratiga 3 dollar"',
            prompt,
        )
        self.assertEqual(
            bot.parse_command(normalized)["purchase_price"],
            462_600.0,
        )

    def test_transcription_skips_a_client_where_model_is_not_found(self):
        audio_path = Path(self.temp_dir.name) / "sample.ogg"
        audio_path.write_bytes(b"test audio")
        unavailable_client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(
                    side_effect=RuntimeError(
                        "404 NOT_FOUND model is no longer available"
                    )
                )
            )
        )
        working_client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(
                    return_value=SimpleNamespace(text="Prado 3x4 2 dona keldi")
                )
            )
        )

        with patch.object(
            bot, "gemini_clients", [unavailable_client, working_client]
        ), patch.object(bot, "inventory_list_full", return_value=[]):
            normalized = bot.gemini_transcribe(audio_path)

        self.assertEqual(normalized, "Prado 3x4 2 dona keldi")
        unavailable_client.models.generate_content.assert_called_once()
        working_client.models.generate_content.assert_called_once()

    def test_transcription_falls_back_to_next_model_when_configured_model_is_not_found(self):
        audio_path = Path(self.temp_dir.name) / "sample2.ogg"
        audio_path.write_bytes(b"test audio")

        def fake_generate_content(*args, **kwargs):
            model = kwargs.get("model")
            if model == "gemini-1.5-pro":
                raise RuntimeError("404 NOT_FOUND models/gemini-1.5-pro is not found")
            return SimpleNamespace(text="Prado 3x4 5 dona keldi")

        mock_client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(side_effect=fake_generate_content)
            )
        )

        with patch.object(bot, "gemini_clients", [mock_client]), patch.object(
            bot, "inventory_list_full", return_value=[]
        ), patch.object(bot, "GEMINI_MODEL", "gemini-1.5-pro"):
            normalized = bot.gemini_transcribe(audio_path)

        self.assertEqual(normalized, "Prado 3x4 5 dona keldi")

    def test_pending_preview_shows_name_dimensions_quantity_and_total_square_price(self):
        bot.set_usd_rate(12_850)
        parsed = bot.parse_command(
            "Prado 3 ga 4 5 dona keldi "
            "kelish narxi 3 dollardan kvadratiga"
        )
        pending = {
            "name": parsed["name"],
            "quantity": parsed["quantity"],
            "unit": parsed["unit"],
            "dimensions": parsed["dimensions"],
            "area_sqm": parsed["area_sqm"],
            "price_per_sqm_usd": parsed["price_per_sqm_usd"],
            "purchase_price_usd": parsed["purchase_price_usd"],
            "purchase_price": parsed["purchase_price"],
        }

        preview, _ = bot.pending_preview(pending)

        self.assertIn("Nomi: Prado", preview)
        self.assertIn("O‘lchami: 3x4 (12.0 m²)", preview)
        self.assertIn("Miqdor: 5 dona", preview)
        self.assertIn("Kvadrat narxi: $3.00", preview)
        self.assertIn("Kelish narxi: $36.00", preview)
        self.assertIn("462 600 so'm", preview)

    async def test_text_edit_prompt_shows_current_fields_and_name_only_instruction(self):
        item = bot.inventory_create(
            "Katta Qizil Gul 3x4",
            "dona",
            5,
            462_600,
            600_000,
            dimensions="3x4",
            area_sqm=12,
            price_per_sqm_usd=3,
            purchase_price_usd=36,
        )
        query = SimpleNamespace(
            data=f"wh:textedit:{item['id']}",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={})

        await bot.warehouse_callback(
            SimpleNamespace(callback_query=query),
            context,
        )

        prompt = query.edit_message_text.await_args.args[0]
        self.assertIn("Nomi: Katta Qizil Gul", prompt)
        self.assertIn("O‘lchami: 3x4 (12 m²)", prompt)
        self.assertIn("Miqdori: 5 dona", prompt)
        self.assertIn("Kvadrat narxi: $3.00", prompt)
        self.assertIn("Kelish narxi: 462 600 so‘m", prompt)
        self.assertIn("Sotish narxi: 600 000 so‘m", prompt)
        self.assertIn("yangi nomni o‘zi yuboring", prompt)

    async def test_name_only_text_edit_preserves_dimensions_and_prices(self):
        bot.set_usd_rate(12_850)
        item = bot.inventory_create(
            "Katta Qizil Gul 3x4",
            "dona",
            5,
            462_600,
            600_000,
            dimensions="3x4",
            area_sqm=12,
            price_per_sqm_usd=3,
            purchase_price_usd=36,
        )
        context = SimpleNamespace(user_data={"editing_warehouse_id": item["id"]})
        message = SimpleNamespace(
            text="Katta Qizil Guli",
            reply_text=AsyncMock(),
        )

        await bot.handle_text(
            SimpleNamespace(message=message, effective_message=message),
            context,
        )

        preview = message.reply_text.await_args.args[0]
        self.assertIn("Nomi: Katta Qizil Guli", preview)
        self.assertIn("O‘lchami: 3x4 (12 m²)", preview)
        self.assertIn("Kvadrat narxi: $3.00", preview)
        self.assertEqual(context.user_data["pending_warehouse_edit"]["quantity"], 5.0)

        query = SimpleNamespace(
            data="wh:save-edit",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        await bot.warehouse_callback(
            SimpleNamespace(callback_query=query),
            context,
        )
        updated = bot.inventory_get(item["id"])
        self.assertEqual(updated["name"], "Katta Qizil Guli")
        self.assertEqual(updated["dimensions"], "3x4")
        self.assertEqual(updated["area_sqm"], 12.0)
        self.assertEqual(updated["price_per_sqm_usd"], 3.0)
        current_prices = bot.inventory_get_with_prices(item["id"])
        self.assertEqual(current_prices["purchase_price"], 462_600.0)
        self.assertEqual(current_prices["sale_price"], 600_000.0)

    def test_usd_rate_getting_and_setting(self):
        # Default rate
        self.assertEqual(bot.get_usd_rate(), 12850.0)

        # Manual rate setting
        bot.set_usd_rate(12950.0)
        self.assertEqual(bot.get_usd_rate(), 12950.0)

        # Recalculates dollar equivalent based on new rate
        res = bot.parse_command("Elen 3x4 keldi kvadrati 3 dollar")
        # 12 m2 * 3$ = 36$ * 12950 = 466,200
        self.assertEqual(res["purchase_price"], 466200.0)

    def test_variable_price_sale_calculates_sale_profit_and_cumulative_model_profit(self):
        bot.set_usd_rate(12850.0)
        # Create carpet with $36 purchase price (462,600 so'm)
        product = bot.inventory_create(
            "Sag 3x4",
            "dona",
            5.0,
            purchase_price=462600.0,
            sale_price=600000.0,
            dimensions="3x4",
            area_sqm=12.0,
            price_per_sqm_usd=3.0,
            purchase_price_usd=36.0,
        )

        # Sale 1: Sold at 550,000 so'm
        new_q, status, sale1 = bot.inventory_sell("Sag 3x4", 1.0, sale_price=550000.0)
        self.assertEqual(status, "ok")
        self.assertEqual(new_q, 4.0)
        self.assertEqual(sale1["profit"], 550000.0 - 462600.0)  # 87,400 so'm
        self.assertEqual(sale1["total_profit"], 87400.0)
        self.assertEqual(sale1["total_profit_usd"], round(87400.0 / 12850.0, 2))

        # Sale 2: Sold at 650,000 so'm
        new_q2, status2, sale2 = bot.inventory_sell("Sag 3x4", 1.0, sale_price=650000.0)
        self.assertEqual(status2, "ok")
        self.assertEqual(new_q2, 3.0)
        self.assertEqual(sale2["profit"], 650000.0 - 462600.0)  # 187,400 so'm
        # Cumulative profit is 87,400 + 187,400 = 274,800 so'm
        self.assertEqual(sale2["total_profit"], 274800.0)
        self.assertEqual(sale2["total_profit_usd"], round(274800.0 / 12850.0, 2))

        # Check success message formatting
        msg = bot.format_sale_success_message(
            "Sag 3x4", 1.0, "dona", 3.0, sale2
        )
        self.assertIn("Shu sotuvdan foyda: +187 400 so'm", msg)
        self.assertIn("Shu gilam bo‘yicha jami foyda: +274 800 so'm", msg)

    def test_renaming_carpet_allows_name_of_archived_carpet(self):
        # Carpet 1: Sag
        p1 = bot.inventory_create("Sag", "dona", 5.0, 100_000.0, 150_000.0)
        self.assertEqual(p1["name"], "Sag")

        # Archive carpet 1
        self.assertTrue(bot.inventory_archive(p1["id"]))

        # Carpet 2: sag 3x4 4 dona kelish narxi 4$
        p2 = bot.inventory_create("sag 3x4 4 dona kelish narxi 4$", "dona", 4.0, 100_000.0, 150_000.0)

        # Renaming carpet 2 to "Sag" or "sag 10 metr" should succeed without "name_exists" conflict
        qty, status = bot.inventory_update(p2["id"], "Sag", "metr", 10.0)
        self.assertEqual(status, "ok")
        self.assertEqual(qty, 10.0)

        updated = bot.inventory_get(p2["id"])
        self.assertEqual(updated["name"], "Sag")
        self.assertEqual(updated["unit"], "metr")

    def test_inventory_delete_permanently_removes_carpet(self):
        item = bot.inventory_create("O'chiriladigan gilam", "dona", 8.0, 50_000.0, 90_000.0)
        bot.inventory_sell("O'chiriladigan gilam", 2.0)

        # Verify movements and prices exist
        self.assertTrue(len(bot.movement_history(item["id"])) > 0)

        # Permanently delete
        deleted = bot.inventory_delete(item["id"])
        self.assertTrue(deleted)

        # Verify completely gone
        self.assertIsNone(bot.inventory_get(item["id"]))
        self.assertEqual(len(bot.movement_history(item["id"])), 0)

    async def test_wizard_step2_instant_unit_and_quantity_button(self):
        # 1. Wizard with instant dona button
        query_qu_dona = SimpleNamespace(
            data="wiz:qu:5:dona",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={"wizard_data": {"name": "Afshon Shohona"}}
        )
        await bot.wizard_callback(
            SimpleNamespace(callback_query=query_qu_dona, effective_message=query_qu_dona),
            context,
        )
        self.assertIn("pending_inventory", context.user_data)
        pending = context.user_data["pending_inventory"]
        self.assertEqual(pending["name"], "Afshon Shohona")
        self.assertEqual(pending["quantity"], 5.0)
        self.assertEqual(pending["unit"], "dona")

        # 2. Wizard with instant metr button
        query_qu_metr = SimpleNamespace(
            data="wiz:qu:7:metr",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context2 = SimpleNamespace(
            user_data={"wizard_data": {"name": "Dorojka Uzun"}}
        )
        await bot.wizard_callback(
            SimpleNamespace(callback_query=query_qu_metr, effective_message=query_qu_metr),
            context2,
        )
        self.assertIn("pending_inventory", context2.user_data)
        pending2 = context2.user_data["pending_inventory"]
        self.assertEqual(pending2["name"], "Dorojka Uzun")
        self.assertEqual(pending2["quantity"], 7.0)
        self.assertEqual(pending2["unit"], "metr")

    async def test_wizard_step2_text_with_unit_stages_immediately(self):
        context = SimpleNamespace(
            user_data={"wizard_step": "quantity", "wizard_data": {"name": "Samarqand"}}
        )
        message = SimpleNamespace(text="10 dona", reply_text=AsyncMock())
        await bot.handle_text(
            SimpleNamespace(message=message, effective_message=message),
            context,
        )
        self.assertIn("pending_inventory", context.user_data)
        self.assertEqual(context.user_data["pending_inventory"]["quantity"], 10.0)
        self.assertEqual(context.user_data["pending_inventory"]["unit"], "dona")

    async def test_voice_prompt_includes_price_examples(self):
        target = SimpleNamespace(reply_text=AsyncMock())
        context = SimpleNamespace(user_data={})
        await bot.start_add_inventory_prompt(target, context)
        prompt_text = target.reply_text.await_args.args[0]
        self.assertIn("Narxlari bilan (dollarda)", prompt_text)
        self.assertIn("Narxlari bilan (so‘mda)", prompt_text)

    def test_generate_inventory_csv_bytes(self):
        bot.inventory_create("Test Gilam Export 3x4", "dona", 12.0, 100_000.0, 150_000.0, purchase_price_usd=8.0)
        csv_bytes = bot.generate_inventory_csv_bytes(include_archived=True)
        csv_text = csv_bytes.decode("utf-8-sig")
        lines = csv_text.strip().splitlines()
        self.assertEqual(lines[0], "Nomi;O'lchami;Qoldiq;Birlik;Kelish narxi ($);Holat")
        self.assertIn("Test Gilam Export 3x4;3x4;12;dona;8.0;Faol", csv_text)

    def test_shop_branding_qamashi_gilam(self):
        index_html = (bot.BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Qamashi Gilam №1", index_html)

        # Check welcome text in start
        welcome_text = ""
        def mock_start():
            nonlocal welcome_text
        context = SimpleNamespace(user_data={})
        target = SimpleNamespace(reply_text=AsyncMock())


    def test_voice_sale_dollar_parsing_and_profit(self):
        bot.set_usd_rate(12850.0)
        # Parse command: Qrol 3x7 2 dona sotildi 60 dollardan
        cmd = "Qrol 3x7 2 dona sotildi 60 dollardan"
        parsed = bot.parse_command(cmd)
        self.assertEqual(parsed["action"], "subtract")
        self.assertEqual(parsed["name"], "Qrol 3x7")
        self.assertEqual(parsed["quantity"], 2.0)
        self.assertEqual(parsed["unit"], "dona")
        self.assertEqual(parsed["sale_price_usd"], 60.0)
        self.assertEqual(parsed["sale_price"], 771000.0)
        self.assertIsNone(parsed["purchase_price"])

        # Add carpet to inventory with purchase price $63.00 (809,550 so'm)
        bot.inventory_create(
            "Qrol 3x7",
            "dona",
            10.0,
            purchase_price=809550.0,
            sale_price=None,
            dimensions="3x7",
            area_sqm=21.0,
            purchase_price_usd=63.0,
        )

        # Sell 2 pieces at $60.00 each
        new_q, status, sale = bot.inventory_sell(
            "Qrol 3x7",
            parsed["quantity"],
            sale_price=parsed["sale_price"],
        )
        self.assertEqual(status, "ok")
        self.assertEqual(new_q, 8.0)
        self.assertEqual(sale["sale_price_usd"], 60.0)
        self.assertEqual(sale["purchase_price_usd"], 63.0)
        # Profit per unit: 771,000 - 809,550 = -38,550 so'm (-$3.00)
        # Total profit for 2 pieces: -77,100 so'm (-$6.00)
        self.assertEqual(sale["profit"], -77100.0)
        self.assertEqual(sale["profit_usd"], -6.0)

        # Check message output formatting
        msg = bot.format_sale_success_message(
            "Qrol 3x7", 2.0, "dona", new_q, sale, raw_text=cmd
        )
        self.assertIn("Qrol 3x7", msg)
        self.assertIn("Miqdor: 2 dona", msg)
        self.assertIn("Qoldiq: 8 dona", msg)
        self.assertIn("Sotish narxi: 771 000 so'm ($60.00)", msg)
        self.assertIn("Tannarx (kelishi): 809 550 so'm ($63.00)", msg)
        self.assertIn("Shu sotuvdan foyda: -77 100 so'm (-$6.00)", msg)

    def test_voice_sale_meter_unit_dollar(self):
        bot.set_usd_rate(12850.0)
        parsed = bot.parse_command("Sag 5 metr sotildi 4 dollardan")
        self.assertEqual(parsed["action"], "subtract")
        self.assertEqual(parsed["name"], "Sag")
        self.assertEqual(parsed["unit"], "metr")
        self.assertEqual(parsed["quantity"], 5.0)
        self.assertEqual(parsed["sale_price_usd"], 4.0)
        self.assertEqual(parsed["sale_price"], 51400.0)

    def test_voice_add_piece_dollar_price_not_multiplied_by_area(self):
        bot.set_usd_rate(12850.0)
        parsed = bot.parse_command("Qrol 3x7 5 dona keldi kelish narxi 20 dollar")
        self.assertEqual(parsed["action"], "add")
        self.assertEqual(parsed["name"], "Qrol 3x7")
        self.assertEqual(parsed["quantity"], 5.0)
        self.assertEqual(parsed["unit"], "dona")
        self.assertEqual(parsed["purchase_price_usd"], 20.0)
        self.assertEqual(parsed["purchase_price"], 257000.0)
        self.assertIsNone(parsed["sale_price"])

    def test_voice_add_explicit_sqm_multiplied_by_area(self):
        bot.set_usd_rate(12850.0)
        parsed = bot.parse_command("Sagdan 3ga 4lik kelishi kvadrati 3 dollar")
        self.assertIsNone(parsed["action"])
        self.assertEqual(parsed["price_per_sqm_usd"], 3.0)
        self.assertEqual(parsed["purchase_price_usd"], 36.0)
        self.assertEqual(parsed["purchase_price"], 462600.0)

    def test_voice_add_with_ming_words(self):
        parsed = bot.parse_command("Elen 5 dona keldi kelish 200 ming sotish 300 ming")
        self.assertEqual(parsed["action"], "add")
        self.assertEqual(parsed["name"], "Elen")
        self.assertEqual(parsed["quantity"], 5.0)
        self.assertEqual(parsed["purchase_price"], 200000.0)
        self.assertEqual(parsed["sale_price"], 300000.0)

    def test_voice_sale_55_dollar_profit(self):
        bot.set_usd_rate(12850.0)
        parsed = bot.parse_command("Qrol 3x7 5 dona sotildi 55 dollar")
        self.assertEqual(parsed["action"], "subtract")
        self.assertEqual(parsed["name"], "Qrol 3x7")
        self.assertEqual(parsed["quantity"], 5.0)
        self.assertEqual(parsed["sale_price_usd"], 55.0)
        self.assertEqual(parsed["sale_price"], 706750.0)

        bot.inventory_create(
            "Qrol 3x7 model 2",
            "dona",
            10.0,
            purchase_price=809550.0,
            sale_price=None,
            dimensions="3x7",
            area_sqm=21.0,
            purchase_price_usd=63.0,
        )
        new_q, status, sale = bot.inventory_sell(
            "Qrol 3x7 model 2",
            5.0,
            sale_price=parsed["sale_price"],
        )
        self.assertEqual(status, "ok")
        self.assertEqual(new_q, 5.0)
        self.assertEqual(sale["sale_price_usd"], 55.0)
        self.assertEqual(sale["profit_usd"], -40.0)
        self.assertEqual(sale["profit"], -514000.0)

    def test_voice_sale_mingdan(self):
        parsed = bot.parse_command("Elen 2 dona sotildi 250 mingdan")
        self.assertEqual(parsed["action"], "subtract")
        self.assertEqual(parsed["name"], "Elen")
        self.assertEqual(parsed["quantity"], 2.0)
        self.assertEqual(parsed["sale_price"], 250000.0)

    async def test_sale_without_price_prompts_user_in_handle_text(self):
        bot.inventory_create("Sag Prompt", "dona", 5.0, 100_000.0, None)
        message = SimpleNamespace(
            text="Sag Prompt 2 dona sotildi",
            reply_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={})
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=123))

        await bot.handle_text(update, context)

        self.assertTrue(context.user_data.get("entering_sale_price"))
        pending = context.user_data.get("pending_sale")
        self.assertIsNotNone(pending)
        self.assertEqual(pending["matched"]["name"], "Sag Prompt")
        self.assertEqual(pending["qty"], 2.0)
        message.reply_text.assert_called_once()
        call_args = message.reply_text.call_args
        self.assertIn("qanchaga sotildi?", call_args[0][0])
        markup = call_args[1].get("reply_markup")
        self.assertIsNotNone(markup)
        btn_data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        self.assertIn("sale:skip_price", btn_data)
        self.assertIn("sale:cancel", btn_data)

    async def test_sale_skip_price_callback(self):
        item = bot.inventory_create("Sag Skip Model", "dona", 5.0, 100_000.0, None)
        query = SimpleNamespace(
            data="sale:skip_price",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                "entering_sale_price": True,
                "pending_sale": {
                    "matched": dict(item),
                    "qty": 2.0,
                    "unit": "dona",
                    "purchase_price": 100_000.0,
                    "raw_text": "Sag Skip Model 2 dona sotildi",
                },
            }
        )
        update = SimpleNamespace(callback_query=query)
        await bot.sale_callback(update, context)

        updated = bot.inventory_get(item["id"])
        self.assertEqual(updated["quantity"], 3.0)
        self.assertNotIn("entering_sale_price", context.user_data)
        query.edit_message_text.assert_called_once()
        self.assertIn("Bazadan ayirildi", query.edit_message_text.call_args[0][0])

    async def test_sale_cancel_callback(self):
        item = bot.inventory_create("Sag Cancel Model", "dona", 5.0, 100_000.0, None)
        query = SimpleNamespace(
            data="sale:cancel",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                "entering_sale_price": True,
                "pending_sale": {
                    "matched": dict(item),
                    "qty": 2.0,
                    "unit": "dona",
                },
            }
        )
        update = SimpleNamespace(callback_query=query)
        await bot.sale_callback(update, context)

        updated = bot.inventory_get(item["id"])
        self.assertEqual(updated["quantity"], 5.0)
        self.assertNotIn("entering_sale_price", context.user_data)
        self.assertIn("bekor qilindi", query.edit_message_text.call_args[0][0].lower())

    async def test_entering_sale_price_manual_text(self):
        item = bot.inventory_create("Sag Manual Model", "dona", 5.0, 100_000.0, None)
        message = SimpleNamespace(
            text="150 000",
            reply_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                "entering_sale_price": True,
                "pending_sale": {
                    "matched": dict(item),
                    "qty": 2.0,
                    "unit": "dona",
                    "purchase_price": 100_000.0,
                    "raw_text": "Sag Manual Model 2 dona sotildi",
                },
            }
        )
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=123))

        await bot.handle_text(update, context)

        updated = bot.inventory_get(item["id"])
        self.assertEqual(updated["quantity"], 3.0)
        self.assertNotIn("entering_sale_price", context.user_data)
        message.reply_text.assert_called_once()
        resp = message.reply_text.call_args[0][0]
        self.assertIn("Bazadan ayirildi", resp)
        self.assertIn("150 000 so'm", resp)
        self.assertIn("Shu sotuvdan foyda: +100 000 so'm", resp)

    async def test_product_movement_stats_and_warehouse_history_callback(self):
        item = bot.inventory_create("History Test Model", "dona", 10.0, 50_000.0, 80_000.0)
        bot.inventory_sell("History Test Model", 3.0, sale_price=80_000.0)

        stats = bot.product_movement_stats(item["id"])
        self.assertIsNotNone(stats["first_added"])
        self.assertEqual(stats["total_in"], 10.0)
        self.assertEqual(stats["total_sold"], 3.0)
        self.assertEqual(len(stats["records"]), 2)

        text, markup = bot.warehouse_item_detail_content(dict(item))
        self.assertIn("Omborga qo‘shilgan:", text)
        self.assertIn("Jami kirim: 10 dona", text)
        self.assertIn("Sotilgan: 3 dona", text)
        btn_data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        self.assertIn(f"wh:history:{item['id']}", btn_data)

        query = SimpleNamespace(
            data=f"wh:history:{item['id']}",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(user_data={})
        await bot.warehouse_callback(update, context)

        query.edit_message_text.assert_called_once()
        hist_text = query.edit_message_text.call_args[0][0]
        self.assertIn("History Test Model” harakatlar tarixi:", hist_text)
        self.assertIn("Jami kirim: 10 dona", hist_text)
        self.assertIn("Jami sotilgan: 3 dona", hist_text)


if __name__ == "__main__":
    unittest.main()
