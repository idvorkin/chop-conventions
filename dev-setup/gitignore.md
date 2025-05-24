### Intro/Why?

Having a comprehensive `.gitignore` file is essential for maintaining a clean repository and avoiding accidental commits of sensitive, temporary, or build artifacts.

A well-configured gitignore helps:

- Prevent sensitive files (API keys, credentials) from being committed
- Keep build artifacts and temporary files out of version control
- Reduce repository size and noise
- Improve collaboration by avoiding platform-specific files
- Maintain consistent development environments across team members

### Environment Setup

#### Important Considerations

⚠️ **Before modifying existing `.gitignore`:**

- Always check with the team before changing a pre-existing `.gitignore`
- Review current ignore patterns to understand what's already in place
- Be careful not to accidentally ignore files that should be tracked
- Consider project-specific requirements or customizations
- Test with `git status` after changes to ensure nothing important is ignored

⚠️ **Platform and IDE considerations:**

- The configuration below covers many common scenarios but may not fit every project
- **Languages covered**: Python, Node.js, Java, C/C++, Go, Rust, Ruby, PHP, .NET, Swift
- **IDEs covered**: VSCode, IntelliJ/JetBrains, Vim, Emacs, Sublime Text, Atom
- **Platforms covered**: macOS, Windows, Linux
- **Build tools covered**: Maven, Gradle, npm, yarn, pip, cargo, composer, etc.

**Consider adding project-specific patterns for:**

- Custom build outputs
- Local configuration files
- Database files
- Generated documentation
- Deployment artifacts

#### Default Configuration

`.gitignore`

```gitignore
# ==============================================================================
# SECURITY & CREDENTIALS
# ==============================================================================
*.pem
*.key
*.p12
*.p8
*.cer
*.crt
*.der
*.pfx
.env
.env.local
.env.*.local
.env.production
.env.staging
.env.development
secrets.json
config/secrets.yml
.aws/credentials
.aws/config
gcp-keyfile.json
azure-credentials.json
*.secrets
*secret*
*password*
*credentials*
*token*
.netrc

# ==============================================================================
# MACOS
# ==============================================================================
.DS_Store
.DS_Store?
._*
.Spotlight-V100
.Trashes
ehthumbs.db
Thumbs.db
.AppleDouble
.LSOverride
Icon
.DocumentRevisions-V100
.fseventsd
.TemporaryItems
.VolumeIcon.icns
.com.apple.timemachine.donotpresent
.AppleDB
.AppleDesktop
Network Trash Folder
Temporary Items
.apdisk

# ==============================================================================
# WINDOWS
# ==============================================================================
Thumbs.db
ehthumbs.db
Desktop.ini
$RECYCLE.BIN/
*.cab
*.msi
*.msm
*.msp
*.lnk

# ==============================================================================
# LINUX
# ==============================================================================
*~
.fuse_hidden*
.directory
.Trash-*
.nfs*

# ==============================================================================
# EDITORS & IDES
# ==============================================================================

# VSCode
.vscode/
!.vscode/settings.json
!.vscode/tasks.json
!.vscode/launch.json
!.vscode/extensions.json
*.code-workspace
.history/

# IntelliJ IDEA / JetBrains
.idea/
*.iws
*.iml
*.ipr
out/
.idea_modules/
atlassian-ide-plugin.xml
com_crashlytics_export_strings.xml
crashlytics.properties
crashlytics-build.properties
fabric.properties

# Vim
*.swp
*.swo
*~
.netrwhist
Session.vim
.vim

# Emacs
\#*\#
/.emacs.desktop
/.emacs.desktop.lock
*.elc
auto-save-list
tramp
.\#*
.org-id-locations
*_archive
*_flymake.*
/eshell/history
/eshell/lastdir
/elpa/
*.rel
/auto/
.cask/
dist/
flycheck_*.el
.projectile
.dir-locals.el
/network-security.data

# Sublime Text
*.tmlanguage.cache
*.tmPreferences.cache
*.stTheme.cache
*.sublime-workspace
*.sublime-project
sftp-config.json
Package Control.last-run
Package Control.ca-list
Package Control.ca-bundle
Package Control.system-ca-bundle
Package Control.cache/
Package Control.ca-certs/
Package Control.merged-ca-bundle
Package Control.user-ca-bundle
oscrypto-ca-bundle.crt
bh_unicode_properties.cache

# Atom
.atom/

# ==============================================================================
# PROGRAMMING LANGUAGES
# ==============================================================================

# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
pip-wheel-metadata/
share/python-wheels/
*.egg-info/
.installed.cfg
*.egg
MANIFEST
*.manifest
*.spec
pip-log.txt
pip-delete-this-directory.txt
.tox/
.nox/
.coverage
.coverage.*
.cache
nosetests.xml
coverage.xml
*.cover
*.py,cover
.hypothesis/
.pytest_cache/
.mypy_cache/
.pyre/
.pytype/
cython_debug/
PyCharm/

# Node.js
node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*
lerna-debug.log*
.pnpm-debug.log*
report.[0-9]*.[0-9]*.[0-9]*.[0-9]*.json
pids
*.pid
*.seed
*.pid.lock
lib-cov
coverage
.nyc_output
.grunt
bower_components
.lock-wscript
build/Release
.npm
.eslintcache
.stylelintcache
.rpt2_cache/
.rts2_cache_cjs/
.rts2_cache_es/
.rts2_cache_umd/
.node_repl_history
*.tgz
.yarn-integrity
.env.build
.next
out/
.nuxt
dist
.cache/
.parcel-cache/
.tmp
.runtimeconfig.json
jspm_packages/

# Java
*.class
*.war
*.nar
*.ear
*.zip
*.tar.gz
*.rar
hs_err_pid*
target/
.mvn/timing.properties
.mvn/wrapper/maven-wrapper.jar
.gradle
**/build/
!src/**/build/
gradle-app.setting
!gradle-wrapper.jar
.gradletasknamecache

# C/C++
*.o
*.ko
*.obj
*.elf
*.ilk
*.map
*.exp
*.gch
*.pch
*.lib
*.a
*.la
*.lo
*.dll
*.exe
*.out
*.app
*.i*86
*.x86_64
*.hex
*.dSYM/
*.su
*.idb
*.pdb
*.mod*
*.cmd
.tmp_versions/
modules.order
Module.symvers
Mkfile.old
dkms.conf

# Go
*.exe~
vendor/
Godeps/

# Rust
target/
**/*.rs.bk
*.pdb
Cargo.lock

# Ruby
*.gem
*.rbc
/.config
/coverage/
/InstalledFiles
/pkg/
/spec/reports/
/spec/examples.txt
/test/tmp/
/test/version_tmp/
/tmp/
.document
.precomp
.rdoc
.yardoc
/doc/
/rdoc/
/.bundle/
/.yardoc
/_yardoc/
/Gemfile.lock
.rbenv-version
.rvmrc
/.byebug_history

# PHP
/vendor/
composer.phar
composer.lock
.env.php
.phpunit.result.cache

# .NET
bin/
obj/
*.user
*.userosscache
*.sln.docstates
*.userprefs
.vs/
.vscode/
[Aa]sp[Nn]et[Cc]ore.dll
[Oo]bj/
[Ll]og/
[Ll]ogs/
.nuget/
BenchmarkDotNet.Artifacts/
project.lock.json
project.fragment.lock.json
artifacts/
**/Properties/launchSettings.json
*.tmp
*.tmp_proj
*_wpftmp.csproj
*.log
*.tlog
*.vspscc
*.vssscc
.builds
*.pidb
*.svclog
*.scc
_Chutzpah*

# Swift
.build/
Packages/
*.xcodeproj
!default.xcworkspace
*.xcworkspace/xcshareddata/
*.xcworkspace/xcuserdata/
DerivedData/
.swiftpm/xcode/package.xcworkspace/contents.xcworkspacedata
.netrc

# ==============================================================================
# DATABASES
# ==============================================================================
*.sqlite
*.sqlite3
*.db
*.db3
*.s3db
*.sl3
*.sql
dump.rdb
*.rdb
*.dump

# ==============================================================================
# LOGS & TEMPORARY FILES
# ==============================================================================
logs
*.log
log/
*.tmp
*.temp
*.swp
*.bak
*.orig
*.rej
*~
.sass-cache/
.tmp/
.cache/
tmp/
temp/

# ==============================================================================
# BUILD TOOLS & PACKAGE MANAGERS
# ==============================================================================

# Webpack
.webpack/
webpack-stats.json

# Rollup
rollup.config.js.map

# Parcel
.parcel-cache/

# Vite
dist-ssr
*.local

# Docker
.dockerignore
Dockerfile.*

# Terraform
*.tfstate
*.tfstate.*
.terraform/
.terraform.lock.hcl
*.tfvars
override.tf
override.tf.json
*_override.tf
*_override.tf.json
.terraformrc
terraform.rc

# ==============================================================================
# TESTING & COVERAGE
# ==============================================================================
coverage/
.coverage
htmlcov/
.tox/
.pytest_cache/
.testmondata
test-results/
junit.xml
coverage.xml
*.cover
.hypothesis/
.cache

# ==============================================================================
# DEPLOYMENT & CI/CD
# ==============================================================================
.serverless/
.amplify/
.vercel/
.netlify/
.now/
.firebase/
dist-prod/
build-prod/

# ==============================================================================
# DOCUMENTATION
# ==============================================================================
docs/_build/
docs/build/
site/
_site/
.docusaurus/
.vuepress/dist/

# ==============================================================================
# MISC
# ==============================================================================
.venv/
venv/
env/
ENV/
.editorconfig
.tool-versions
.nvmrc
.python-version
.ruby-version
.terraform-version
*.hprof
.htaccess
.well-known/
sitemap.xml
robots.txt
```

#### Customization Tips

After creating the base `.gitignore`, consider:

1. **Review and trim**: Remove sections irrelevant to your project
2. **Add project-specific patterns**: Include custom build outputs, config files, etc.
3. **Use `git check-ignore -v <file>`** to debug ignore patterns
4. **Use global gitignore** for personal preferences: `git config --global core.excludesfile ~/.gitignore_global`
5. **Be careful with wildcards**: Test patterns thoroughly to avoid over-ignoring

#### Validate Your Setup

```bash
# Check what's being ignored
git status --ignored

# Test specific files
git check-ignore -v <filename>

# See what would be added (dry run)
git add -n .
```
