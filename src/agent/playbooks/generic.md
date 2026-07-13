# Playbook: Generalist setup engineer (any stack — no family specialist)

Scope: ANY repo whose stack has no dedicated specialist — Java/Maven/Gradle, Kotlin, Scala/sbt,
Elixir/mix, Ruby/Bundler, PHP/Composer, .NET, an unusual native/CMake build, a polyglot mix.
The goal is the same as every specialist: get the repo **set up and actually running**, proven
by a real check — not assumed.

## Principle: READ, then REASON, then VERIFY
There is no hand-coded recipe for this stack, so **read the repo's own build files and reason
the setup** (this is the reasoning core, not a guess):
- Detect the toolchain from the manifest the repo ships:
  - `pom.xml` → Maven (`mvn -q -DskipTests package`, run via `java -jar target/*.jar` or the declared main class).
  - `build.gradle`/`build.gradle.kts` + `gradlew` → Gradle (`./gradlew build`, `./gradlew run`/`bootRun`).
  - `build.sbt` → sbt; `mix.exs` → Elixir mix; `Gemfile` → Bundler; `composer.json` → Composer;
    `*.csproj`/`*.sln` → .NET; `CMakeLists.txt`/`Makefile` → native build.
- Read the README + the build file for the declared build/run/test commands; prefer the repo's
  OWN documented commands over inventing any.
- Probe the toolchain on the target (`java -version`, `mvn -v`, `gradle -v`, etc.); install the
  required runtime/version when it's missing, the same way the language specialists do.

## Run + verify (the outcome, not "it started")
- A web/service repo → start it and probe the served URL/health path for a healthy response.
- A CLI/library → run its OWN declared test/smoke (the build's `test` task), judge by exit code.
- Never report "running" without a real outcome check.

## Honesty
- A genuinely missing tool, a paid secret/license, or hardware (GPU) the target lacks is an
  HONEST ASK, not a fabricated step.
- Depth scales with the configured model — this playbook is the SHAPE; the reasoning pass fills
  in the specifics from the actual repo.
