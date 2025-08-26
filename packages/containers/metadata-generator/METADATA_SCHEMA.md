# fdnix Metadata Schema

This document describes the enhanced metadata schema used by fdnix after implementing the `--meta` flag support for `nix-env`. The schema includes rich package metadata from nixpkgs that enables better search, filtering, and categorization.

## Overview

The metadata extraction uses `nix-env -f <nixpkgs> -qaP --json --meta` to gather comprehensive package information. The `--meta` flag provides access to structured metadata that was previously unavailable.

## Base Package Information

### Core Fields
- **packageName** (string): Primary key - the package name extracted from `pname` or derived from attribute path
- **version** (string): Sort key - package version, defaults to "unknown" if not available
- **attributePath** (string): Full nixpkgs attribute path (e.g., "nixpkgs.hello", "home-manager.home-manager")
- **lastUpdated** (ISO string): Timestamp when metadata was extracted
- **hasEmbedding** (boolean): Flag indicating if package has vector embeddings for search

### Descriptive Fields
- **description** (string): Short package description, sanitized and limited to 2000 chars
- **longDescription** (string): Detailed package description when available from meta
- **homepage** (string): Package homepage URL, sanitized
- **mainProgram** (string): Primary executable name (new with --meta)
- **position** (string): Source code position reference for debugging

## Enhanced Metadata Fields (from --meta)

### Availability Status
- **available** (boolean): Whether package is available for installation (default: true)
- **broken** (boolean): Whether package is marked as broken
- **insecure** (boolean): Whether package has known security issues
- **unsupported** (boolean): Whether package is unsupported on current platform
- **unfree** (boolean): Whether package has non-free license

### License Information

The license field now supports structured license data:

```javascript
// Simple string license
{
  "type": "string",
  "value": "MIT"
}

// Structured license object
{
  "type": "object",
  "shortName": "mit",
  "fullName": "MIT License", 
  "spdxId": "MIT",
  "url": "https://spdx.org/licenses/MIT.html",
  "free": true,
  "redistributable": true,
  "deprecated": false
}

// Multiple licenses
{
  "type": "array",
  "licenses": [
    {
      "shortName": "gpl2Plus",
      "fullName": "GNU General Public License v2.0 or later",
      "spdxId": "GPL-2.0-or-later",
      // ... other fields
    }
  ]
}
```

### Maintainer Information

Enhanced maintainer structure with detailed contact information:

```javascript
[
  {
    "name": "Robert Helgesson",
    "email": "robert@rycee.net", 
    "github": "rycee",
    "githubId": 798147
  }
]
```

### Platform Support
- **platforms** (array): List of supported platforms (e.g., "x86_64-linux", "aarch64-darwin")
- **outputsToInstall** (array): Default outputs to install (typically ["out"])

## Sample Complete Record

```json
{
  "packageName": "home-manager",
  "version": "25.05",
  "attributePath": "home-manager.home-manager",
  "description": "A user environment configurator",
  "longDescription": "Home Manager provides a basic system for managing a user environment using Nix package manager together with the Nix libraries found in Nixpkgs.",
  "homepage": "https://github.com/nix-community/home-manager",
  "mainProgram": "home-manager",
  "position": "/nix/store/.../home-manager/default.nix:35",
  "license": {
    "type": "object",
    "shortName": "mit",
    "fullName": "MIT License",
    "spdxId": "MIT",
    "url": "https://spdx.org/licenses/MIT.html",
    "free": true,
    "redistributable": true,
    "deprecated": false
  },
  "maintainers": [
    {
      "name": "Robert Helgesson",
      "email": "robert@rycee.net",
      "github": "rycee", 
      "githubId": 798147
    }
  ],
  "platforms": [
    "x86_64-darwin",
    "aarch64-darwin", 
    "aarch64-linux",
    "x86_64-linux",
    // ... more platforms
  ],
  "available": true,
  "broken": false,
  "insecure": false,
  "unsupported": false,
  "unfree": false,
  "outputsToInstall": ["out"],
  "lastUpdated": "2025-08-26T10:30:00.000Z",
  "hasEmbedding": false
}
```

## Embedding Generation Impact

The enhanced metadata improves search quality in several ways:

### Text Representation for Embeddings
1. **longDescription** preferred over **description** for richer semantic content
2. **mainProgram** included for better command-line tool discoverability  
3. **Enhanced license formatting** with proper names and SPDX identifiers
4. **Structured maintainer names** from the enhanced maintainer objects

### Filtering Capabilities
The embedding generation can optionally exclude problematic packages:
- **available=false**: Package not available for installation
- **broken=true**: Known broken packages
- **insecure=true**: Packages with security vulnerabilities  
- **unsupported=true**: Platform-unsupported packages

This filtering ensures search results focus on usable packages while maintaining comprehensive metadata for all packages in the database.

## Migration Notes

- Existing packages without enhanced metadata will continue to work with fallback to top-level fields
- The **license** field format has changed from simple strings to structured objects
- The **maintainers** field now contains objects instead of simple strings
- New boolean fields default to safe values (available=true, broken=false, etc.)

## References

- [Nixpkgs Meta Attributes Documentation](https://ryantm.github.io/nixpkgs/stdenv/meta/)
- [SPDX License List](https://spdx.org/licenses/)
- [nix-env Manual](https://nixos.org/manual/nix/stable/command-ref/nix-env.html)