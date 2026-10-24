import java.text.SimpleDateFormat
import java.util.Date
import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

// Tek sürüm kaynağı: ../client/version.json {"version":"X.Y.Z"} -> versionName; versionCode = X*10000 + Y*100 + Z.
val dzVersion: String = Regex("\"version\"\\s*:\\s*\"([0-9]+\\.[0-9]+\\.[0-9]+)\"")
    .find(rootProject.file("../client/version.json").readText())?.groupValues?.get(1)
    ?: error("client/version.json: \"version\": \"X.Y.Z\" bulunamadı")
val dzVersionCode: Int = dzVersion.split(".").map { it.toInt() }.let { (a, b, c) -> a * 10000 + b * 100 + c }

// Release imzası: git DIŞI ~/.diziflix/keystore.properties (storeFile, storePassword, keyAlias, keyPassword).
// Dosya yoksa release imzasız çıkar (uyarı); debug derleme etkilenmez. Keystore kaybolursa güncelleme zinciri kırılır: yedekle.
val dzKeystoreProps = File(System.getProperty("user.home"), ".diziflix/keystore.properties")
val dzSigning: Properties? = if (dzKeystoreProps.isFile) Properties().apply { dzKeystoreProps.inputStream().use { load(it) } } else null
if (dzSigning == null) logger.warn("diziflix: ${dzKeystoreProps} yok -> release APK imzasız (yalnız debug imzalı kurulabilir)")

android {
    namespace = "com.diziflix.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.diziflix.app"
        // 21 (Lollipop): WebView Play'den güncellenebilir, MSE (hls.js) var; eski TV kutularını da kapsar.
        minSdk = 21
        targetSdk = 34
        versionCode = dzVersionCode
        versionName = dzVersion
    }

    signingConfigs {
        if (dzSigning != null) {
            create("release") {
                storeFile = file(dzSigning.getProperty("storeFile"))
                storePassword = dzSigning.getProperty("storePassword")
                keyAlias = dzSigning.getProperty("keyAlias")
                keyPassword = dzSigning.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // Kalıcı release anahtarı (~/.diziflix); otomatik güncelleme aynı imzayı ister.
            signingConfig = if (dzSigning != null) signingConfigs.getByName("release") else null
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    lint {
        abortOnError = false
    }
}

// Web istemcisi (../client) derlemede varlıklara kopyalanır; git'te çoğaltılmaz (.gitignore).
val syncClientAssets by tasks.registering(Sync::class) {
    from(rootProject.file("../client")) {
        exclude("tests/**", "README*", "build-wgt.sh", "config.xml", "version.json", "**/.*", "**/.DS_Store")
    }
    exclude("js/version.js")
    into(layout.projectDirectory.dir("src/main/assets/client"))
    // version.js sürüm kaynağından yazılır (DZ_VERSION = versionName; DZ_BUILD = derleme damgası)
    val out = layout.projectDirectory.file("src/main/assets/client/js/version.js").asFile
    val ver = dzVersion
    inputs.property("dzVersion", ver)
    doLast {
        val stamp = SimpleDateFormat("yyyy-MM-dd HH:mm").format(Date())
        out.writeText("/* version.js - Android derlemesi (syncClientAssets) yazar. */\nwindow.DZ_VERSION='$ver';\nwindow.DZ_BUILD='$stamp';\n")
    }
}
tasks.named("preBuild") { dependsOn(syncClientAssets) }

dependencies {
    implementation(libs.androidx.webkit)
}
