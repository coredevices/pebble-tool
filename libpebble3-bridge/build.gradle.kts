plugins {
    kotlin("jvm") version "2.0.21"
    kotlin("plugin.serialization") version "2.0.21"
    id("com.github.johnrengelman.shadow") version "8.1.1"
    application
}

repositories {
    mavenCentral()
}

dependencies {
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.9.0")
}

kotlin {
    jvmToolchain(21)
    compilerOptions {
        freeCompilerArgs.add("-Xexpect-actual-classes")
        optIn.addAll(
            "kotlin.ExperimentalUnsignedTypes",
            "kotlin.ExperimentalStdlibApi",
            "kotlin.uuid.ExperimentalUuidApi",
            "kotlin.time.ExperimentalTime",
            "kotlinx.coroutines.ExperimentalCoroutinesApi",
            "kotlinx.serialization.ExperimentalSerializationApi"
        )
    }
}

application {
    mainClass.set("io.rebble.libpebblecommon.bridge.MainKt")
}

tasks.shadowJar {
    archiveBaseName.set("libpebble3-bridge")
    archiveClassifier.set("all")
    archiveVersion.set("")
}

tasks.named("distZip") { dependsOn(tasks.shadowJar) }
tasks.named("distTar") { dependsOn(tasks.shadowJar) }
tasks.named("startScripts") { dependsOn(tasks.shadowJar) }
