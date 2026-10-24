from bot.keyboards.messages import messages_keyboard


def test_messages_keyboard_contains_back_button():
    keyboard = messages_keyboard([])

    back_button = keyboard.inline_keyboard[-1][0]
    assert back_button.text == "⬅️ رجوع"
    assert back_button.callback_data == "menu:main"


def test_message_input_keyboard_contains_only_back_button():
    keyboard = messages_keyboard([], include_add=False)

    assert len(keyboard.inline_keyboard) == 1
    assert keyboard.inline_keyboard[0][0].callback_data == "messages:list"
