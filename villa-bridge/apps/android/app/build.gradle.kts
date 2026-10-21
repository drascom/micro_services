import java.net.URI
import java.security.MessageDigest

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val nodeMobileVersion = "18.20.4"
val nodeMobileSha256 = "bd7321eaa1a7602fbe0bb87302df2d79d87835cf4363fbdd17c350dbb485c2af"
val nodeMobileZip = layout.buildDirectory.file("downloads/nodejs-mobile-$nodeMobileVersion-android.zip")
val nodeMobileDir = layout.buildDirectory.dir("node-mobile")
val generatedNodeAssets = layout.buildDirectory.dir("generated/node-assets")

val downloadNodeMobile by tasks.registering {
    outputs.file(nodeMobileZip)
    doLast {
        val target = nodeMobileZip.get().asFile
        target.parentFile.mkdirs()
        if (!target.exists()) {
            val url = URI(
                "https://github.com/nodejs-mobile/nodejs-mobile/releases/download/" +
                    "v$nodeMobileVersion/nodejs-mobile-v$nodeMobileVersion-android.zip"
            ).toURL()
            url.openStream().use { input -> target.outputStream().use(input::copyTo) }
        }
        val digest = MessageDigest.getInstance("SHA-256")
            .digest(target.readBytes())
            .joinToString("") { "%02x".format(it) }
        check(digest == nodeMobileSha256) {
            "Node.js Mobile archive checksum mismatch: $digest"
        }
    }
}

val extractNodeMobile by tasks.registering(Copy::class) {
    dependsOn(downloadNodeMobile)
    from(nodeMobileZip.map { zipTree(it) }) {
        include("include/**")
        include("bin/arm64-v8a/**")
        include("bin/armeabi-v7a/**")
    }
    into(nodeMobileDir)
}

val prepareNodeAssets by tasks.registering(Exec::class) {
    workingDir(rootProject.projectDir.parentFile.parentFile)
    commandLine("npm", "run", "android:prepare")
    val repositoryRoot = rootProject.projectDir.parentFile.parentFile
    inputs.files(
        fileTree(repositoryRoot.resolve("apps/runtime")) {
            exclude("node_modules/**")
        },
        fileTree(rootProject.projectDir.resolve("patches")),
        fileTree(repositoryRoot.resolve("src")),
        fileTree(repositoryRoot.resolve("public")),
        repositoryRoot.resolve("package.json"),
        repositoryRoot.resolve("package-lock.json"),
        repositoryRoot.resolve("tsconfig.json")
    )
    inputs.file(repositoryRoot.resolve("scripts/prepare-android-node.mjs"))
    outputs.dir(generatedNodeAssets)
}

android {
    namespace = "com.villabridge.android"
    compileSdk = 35
    ndkVersion = "27.2.12479018"

    defaultConfig {
        applicationId = "com.villabridge.android"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "0.1-spike"

        ndk {
            abiFilters += listOf("arm64-v8a", "armeabi-v7a")
        }

        externalNativeBuild {
            cmake {
                arguments += listOf(
                    "-DANDROID_STL=c++_shared",
                    "-DNODE_MOBILE_DIR=${nodeMobileDir.get().asFile.absolutePath}"
                )
            }
        }
    }

    sourceSets {
        getByName("main") {
            assets.srcDir(generatedNodeAssets)
            jniLibs.srcDir(nodeMobileDir.map { it.dir("bin") })
        }
    }

    externalNativeBuild {
        cmake {
            path = file("CMakeLists.txt")
            version = "3.22.1"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        buildConfig = true
    }

    androidResources {
        noCompress += "tgz"
    }

    lint {
        disable += "ChromeOsAbiSupport"
    }
}

tasks.named("preBuild") {
    dependsOn(extractNodeMobile, prepareNodeAssets)
}

tasks.configureEach {
    if (name.startsWith("configureCMake") || name.startsWith("buildCMake")) {
        dependsOn(extractNodeMobile)
    }
}
