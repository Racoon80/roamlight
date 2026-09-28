import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "lu.racoon.roamlight"
    compileSdk = 35

    defaultConfig {
        // ⚠ Final. Firebase (push) is registered under this name, and Google
        //   Play knows an app by it for ever -- changing it later makes a
        //   different app. The same as the iPhone's bundle ID.
        applicationId = "lu.racoon.roamlight"
        // ⚠ 26 and not lower: the app needs adaptive icons and the modern
        //   TLS stack. Below that, a self-hosted site with a Let's Encrypt
        //   certificate is not reliably trusted.
        minSdk = 26
        targetSdk = 35
        // ⚠ versionCode only ever goes UP -- it is what the update check
        //   compares (see Updates.kt). 1.1.0 -> 110, 1.1.1 -> 111, 1.2.0 -> 120.
        versionCode = 111
        versionName = "1.1.1"
    }

    // ⚠ The release key is NOT in this repository and never will be. The path
    //   to a properties file (storeFile, storePassword, keyAlias, keyPassword)
    //   comes from outside:  ROAMLIGHT_SIGNING=/path/to/roamlight-release.properties
    //   Lose that key and the app can never be updated in place: Android only
    //   installs an update signed by the same key -- a new key is a new app.
    //   Without it a release build is simply left unsigned.
    val signing = System.getenv("ROAMLIGHT_SIGNING")?.let { path ->
        Properties().apply { file(path).inputStream().use { load(it) } }
    }
    signingConfigs {
        if (signing != null) create("release") {
            storeFile = file(signing.getProperty("storeFile"))
            storePassword = signing.getProperty("storePassword")
            keyAlias = signing.getProperty("keyAlias")
            keyPassword = signing.getProperty("keyPassword")
        }
    }

    buildTypes {
        release {
            if (signing != null) signingConfig = signingConfigs.getByName("release")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"),
                          "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
    packaging { resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" } }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")

    val compose = platform("androidx.compose:compose-bom:2024.12.01")
    implementation(compose)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.navigation:navigation-compose:2.8.5")

    // The QR code on the site's /app page. A whole scanner Activity for two
    // lines of code -- cheaper than plumbing CameraX by hand.
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")

    // Push (a one-line notice when an album grows or is shared). ⚠ Only the
    //   messaging part of Firebase -- no Analytics, no Crashlytics: nothing
    //   about what the family looks at leaves the phone. Set up by hand in
    //   RoamlightApp, so neither google-services.json nor its Gradle plugin
    //   is needed.
    implementation(platform("com.google.firebase:firebase-bom:33.7.0"))
    implementation("com.google.firebase:firebase-messaging")

    testImplementation("junit:junit:4.13.2")

    debugImplementation("androidx.compose.ui:ui-tooling")
    implementation("androidx.compose.ui:ui-tooling-preview")
}
