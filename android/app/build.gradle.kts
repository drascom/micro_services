plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

android {
    namespace = "com.diziflix.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.diziflix.app"
        // 21 (Lollipop): WebView Play'den güncellenebilir, MSE (hls.js) var; eski TV kutularını da kapsar.
        minSdk = 21
        targetSdk = 34
        versionCode = 2
        versionName = "2.0.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // Debug anahtarıyla imzalı: debug <-> release `adb install -r` veriyi silmeden çalışır (yan yükleme).
            signingConfig = signingConfigs.getByName("debug")
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
        exclude("tests/**", "README*", "build-wgt.sh", "config.xml", "**/.*", "**/.DS_Store")
    }
    into(layout.projectDirectory.dir("src/main/assets/client"))
}
tasks.named("preBuild") { dependsOn(syncClientAssets) }

dependencies {
    implementation(libs.androidx.webkit)
}
