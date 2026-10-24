# JS köprüsü (addJavascriptInterface "DZAndroid"): @JavascriptInterface yöntemleri R8 ile silinmesin/yeniden adlandırılmasın.
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}
