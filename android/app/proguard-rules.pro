# Release (R8: minify + shrinkResources) kuralları. Çalışma zamanında doğrulanamayan yerler için
# CÖMERT yazıldı: kuralı fazla geniş tutmak yalnızca APK'yı biraz büyütür, eksik kural ise çalışma
# zamanında ClassNotFound/serileştirme hatasına yol açar.

-keepattributes *Annotation*, InnerClasses, Signature, Exceptions, EnclosingMethod, RuntimeVisibleAnnotations, AnnotationDefault
-keepattributes SourceFile, LineNumberTable
-renamesourcefileattribute SourceFile

# ------------------------------------------------------------------ kotlinx.serialization
# Kütüphane kendi tüketici kurallarını getirir; burada uygulamanın TÜM @Serializable sınıfları
# (modeller, önbellek zarfı, ilerleme kuyruğu) için serializer'lar ve Companion garanti altına alınır.
-dontnote kotlinx.serialization.**
-keepclassmembers class kotlinx.serialization.json.** { *** Companion; }
-keepclasseswithmembers class kotlinx.serialization.json.** { kotlinx.serialization.KSerializer serializer(...); }

-keep,includedescriptorclasses class com.diziflix.app.**$$serializer { *; }
-keepclassmembers class com.diziflix.app.** { *** Companion; }
-keepclasseswithmembers class com.diziflix.app.** { kotlinx.serialization.KSerializer serializer(...); }
-keepclassmembers @kotlinx.serialization.Serializable class com.diziflix.app.** {
    static ** Companion;
    static ** INSTANCE;
    kotlinx.serialization.KSerializer serializer(...);
    private static final ** $$serializer;
    <fields>;
}
# Modeller ve önbellek sınıfları (alan adları JSON anahtarıdır): yeniden adlandırılmasın/silinmesin.
-keep class com.diziflix.app.data.model.** { *; }
-keep class com.diziflix.app.data.cache.** { *; }

# ------------------------------------------------------------------ Media3 (ExoPlayer + UI)
# ExoPlayer bazı bileşenleri yansımayla yükler (RenderersFactory uzantıları, DRM, HLS/DASH parser'ları);
# kütüphane kurallarına ek olarak tümü korunur.
-keep class androidx.media3.** { *; }
-keep interface androidx.media3.** { *; }
-dontwarn androidx.media3.**

# ------------------------------------------------------------------ OkHttp / Okio
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**
-dontwarn okio.**
-keep class okhttp3.internal.publicsuffix.PublicSuffixDatabase { *; }

# ------------------------------------------------------------------ Coil
-dontwarn coil.**

# ------------------------------------------------------------------ Compose / AndroidX / Kotlin
-dontwarn androidx.compose.**
-dontwarn kotlinx.coroutines.debug.**
-keepnames class kotlinx.coroutines.internal.MainDispatcherFactory
-keepnames class kotlinx.coroutines.CoroutineExceptionHandler
-keepclassmembers class kotlinx.coroutines.** { volatile <fields>; }
-dontwarn kotlin.**
-dontwarn javax.annotation.**
-dontwarn org.checkerframework.**
-dontwarn com.google.errorprone.annotations.**
-dontwarn com.google.j2objc.annotations.**

# DataStore (protobuf-lite tabanlı Preferences)
-keepclassmembers class * extends androidx.datastore.preferences.protobuf.GeneratedMessageLite {
    <fields>;
}

# Yayın derlemesinde günlükleme kalkar (uygulamada Log çağrısı yok; ileride eklenirse ayıklanır).
-assumenosideeffects class android.util.Log {
    public static int v(...);
    public static int d(...);
    public static int i(...);
}
