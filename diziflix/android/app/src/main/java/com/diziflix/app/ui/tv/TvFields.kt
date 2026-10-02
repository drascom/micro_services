package com.diziflix.app.ui.tv

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.TextFieldValue
import com.diziflix.app.domain.PlayerKeys

/**
 * TV metin alanı (Tizen `.field` / `.top-search-input` / `.set-input` içi): yalnızca yazı alanı — kutu/çerçeve çağıranın
 * işidir ([onFocusChange] ile odağı bilir). Kumanda davranışı (Aşama 1'den korunur):
 *  - alana odak ekran klavyesini açar; kapatılmışsa Tamam (merkez) yeniden açar;
 *  - Yukarı/Aşağı odağı komşu öğeye taşır; Sağ (imleç sondaysa) / Sol (imleç baştaysa) de komşuya geçer, aksi halde imleç gezer;
 *  - klavyenin "Bitti/Ara" eylemi [onImeAction] (çağıran klavyeyi kapatıp odağı taşır).
 */
@Composable
fun TvTextField(
    value: String,
    onValueChange: (String) -> Unit,
    modifier: Modifier = Modifier,
    placeholder: String = "",
    textSize: Float = 26f,
    weight: FontWeight = FontWeight.Normal,
    placeholderColor: Color = Color(0xFF777777),
    keyboardType: KeyboardType = KeyboardType.Text,
    imeAction: ImeAction = ImeAction.Done,
    onImeAction: () -> Unit = {},
    focusRequester: FocusRequester? = null,
    onFocusChange: (Boolean) -> Unit = {},
) {
    val dims = LocalTvDims.current
    val keyboard = LocalSoftwareKeyboardController.current
    val focusManager = LocalFocusManager.current
    var field by remember { mutableStateOf(TextFieldValue(value, TextRange(value.length))) }
    // Dışarıdan değişen değer (ör. "temizle", kayıtlı adres yüklendi) alana yansır.
    if (field.text != value) field = TextFieldValue(value, TextRange(value.length))
    BasicTextField(
        value = field,
        onValueChange = {
            field = it
            if (it.text != value) onValueChange(it.text)
        },
        singleLine = true,
        textStyle = TextStyle(color = Color.White, fontSize = dims.sp(textSize), fontWeight = weight),
        cursorBrush = SolidColor(TvColors.Accent),
        keyboardOptions = KeyboardOptions(keyboardType = keyboardType, imeAction = imeAction),
        keyboardActions = KeyboardActions(
            onDone = { onImeAction() },
            onSearch = { onImeAction() },
            onGo = { onImeAction() },
        ),
        decorationBox = { inner ->
            Box(contentAlignment = Alignment.CenterStart) {
                if (value.isEmpty() && placeholder.isNotEmpty()) {
                    TvText(placeholder, size = textSize, color = placeholderColor, weight = weight)
                }
                inner()
            }
        },
        modifier = modifier
            .then(if (focusRequester != null) Modifier.focusRequester(focusRequester) else Modifier)
            .onFocusChanged { onFocusChange(it.isFocused) }
            .onPreviewKeyEvent { event ->
                if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                val selection = field.selection
                when {
                    event.nativeKeyEvent.keyCode == PlayerKeys.KEYCODE_DPAD_CENTER -> {
                        keyboard?.show()
                        true
                    }
                    event.key == Key.DirectionUp -> {
                        focusManager.moveFocus(FocusDirection.Up)
                        true
                    }
                    event.key == Key.DirectionDown -> {
                        focusManager.moveFocus(FocusDirection.Down)
                        true
                    }
                    event.key == Key.DirectionRight && selection.collapsed && selection.end >= field.text.length -> {
                        focusManager.moveFocus(FocusDirection.Right)
                        true
                    }
                    event.key == Key.DirectionLeft && selection.collapsed && selection.start <= 0 -> {
                        focusManager.moveFocus(FocusDirection.Left)
                        true
                    }
                    else -> false
                }
            },
    )
}
