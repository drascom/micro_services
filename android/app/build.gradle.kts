plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "com.diziflix.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.diziflix.app"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0.0"
    }

    buildTypes {
        release {
            // Yayın derlemesi: R8 (küçültme + optimizasyon) + kaynak küçültme; hata ayıklanamaz.
            // Debug anahtarıyla imzalı: aynı applicationId + aynı imza, yani debug -> release `install -r`
            // veriyi silmeden çalışır (yan yükleme). Mağaza yayını için gerçek anahtar gerekir.
            isMinifyEnabled = true
            isShrinkResources = true
            isDebuggable = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
        // Compose "strong skipping": kararsız parametreli composable'lar da atlanabilir (örnek eşitliği),
        // lambda'lar otomatik hatırlanır. Kotlin 1.9.24 + Compose Compiler 1.5.14 ile desteklenir.
        freeCompilerArgs += listOf(
            "-P",
            "plugin:androidx.compose.compiler.plugins.kotlin:strongSkipping=true",
        )
        // İsteğe bağlı derleyici raporu: ./gradlew :app:assembleDebug -PcomposeReportsDir=/tmp/rapor
        // (yalnızca kararlılık analizi içindir; varsayılan kapalı, depoda rapor bırakılmaz).
        val composeReportsDir = providers.gradleProperty("composeReportsDir").orNull
        if (composeReportsDir != null) {
            freeCompilerArgs += listOf(
                "-P", "plugin:androidx.compose.compiler.plugins.kotlin:reportsDestination=$composeReportsDir",
                "-P", "plugin:androidx.compose.compiler.plugins.kotlin:metricsDestination=$composeReportsDir",
            )
        }
    }

    buildFeatures {
        compose = true
    }
    composeOptions {
        kotlinCompilerExtensionVersion = libs.versions.composeCompiler.get()
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }

    testOptions {
        unitTests {
            // android.jar saplama sınıflarına dokunan kod JVM testinde çökmesin.
            isReturnDefaultValues = true
        }
    }

    lint {
        abortOnError = false
        // Media3 @UnstableApi kullanımı @OptIn ile işaretli; lint yine de uyarabiliyor.
        disable += "UnsafeOptInUsageError"
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.androidx.profileinstaller)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.foundation)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons.extended)

    implementation(libs.androidx.media3.exoplayer)
    implementation(libs.androidx.media3.exoplayer.hls)
    implementation(libs.androidx.media3.datasource)
    implementation(libs.androidx.media3.ui)

    implementation(libs.guava)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.coil.compose)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver)
}
