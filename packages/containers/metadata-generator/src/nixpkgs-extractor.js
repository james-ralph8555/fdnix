const { execSync, spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

class NixpkgsExtractor {
  constructor() {
    this.nixpkgsPath = '/tmp/nixpkgs';
    this.maxRetries = 3;
    this.retryDelay = 5000; // 5 seconds
  }

  async extractAllPackages() {
    console.log('Cloning nixpkgs repository...');
    await this.cloneNixpkgs();
    
    console.log('Extracting package metadata using nix-env...');
    const rawPackages = await this.extractRawPackageData();
    
    console.log('Processing and cleaning package data...');
    return this.processPackageData(rawPackages);
  }

  async cloneNixpkgs() {
    try {
      // Remove existing directory if it exists
      if (fs.existsSync(this.nixpkgsPath)) {
        execSync(`rm -rf ${this.nixpkgsPath}`, { stdio: 'inherit' });
      }

      // Clone with shallow history for efficiency
      execSync(
        `git clone --depth 1 https://github.com/NixOS/nixpkgs.git ${this.nixpkgsPath}`,
        { 
          stdio: 'inherit',
          timeout: 300000 // 5 minute timeout
        }
      );
      
      console.log('nixpkgs repository cloned successfully');
    } catch (error) {
      throw new Error(`Failed to clone nixpkgs: ${error.message}`);
    }
  }

  async extractRawPackageData() {
    return new Promise((resolve, reject) => {
      const nixEnvProcess = spawn('nix-env', [
        '-f', this.nixpkgsPath,
        '-qaP',
        '--json'
      ], {
        stdio: ['pipe', 'pipe', 'pipe'],
        timeout: 1800000, // 30 minute timeout for large extraction
      });

      let stdout = '';
      let stderr = '';

      nixEnvProcess.stdout.on('data', (data) => {
        stdout += data.toString();
      });

      nixEnvProcess.stderr.on('data', (data) => {
        stderr += data.toString();
      });

      nixEnvProcess.on('close', (code) => {
        if (code !== 0) {
          reject(new Error(`nix-env failed with code ${code}: ${stderr}`));
          return;
        }

        try {
          const packageData = JSON.parse(stdout);
          console.log(`Successfully extracted ${Object.keys(packageData).length} packages`);
          resolve(packageData);
        } catch (parseError) {
          reject(new Error(`Failed to parse nix-env JSON output: ${parseError.message}`));
        }
      });

      nixEnvProcess.on('error', (error) => {
        reject(new Error(`Failed to execute nix-env: ${error.message}`));
      });
    });
  }

  processPackageData(rawPackages) {
    const processedPackages = [];
    const currentTimestamp = new Date().toISOString();

    for (const [packagePath, packageInfo] of Object.entries(rawPackages)) {
      try {
        // Extract package name and attribute path
        const packageName = packageInfo.pname || this.extractPackageNameFromPath(packagePath);
        const version = packageInfo.version || 'unknown';
        
        if (!packageName || packageName === 'unknown') {
          console.warn(`Skipping package with unknown name: ${packagePath}`);
          continue;
        }

        const processedPackage = {
          packageName,
          version,
          attributePath: packagePath,
          description: this.sanitizeString(packageInfo.description || ''),
          homepage: this.sanitizeString(packageInfo.homepage || ''),
          license: this.extractLicenseInfo(packageInfo.license),
          platforms: this.extractPlatforms(packageInfo.platforms),
          maintainers: this.extractMaintainers(packageInfo.maintainers),
          broken: packageInfo.broken || false,
          unfree: packageInfo.unfree || false,
          lastUpdated: currentTimestamp,
          hasEmbedding: false // Will be updated by embedding generator
        };

        processedPackages.push(processedPackage);
        
        if (processedPackages.length % 1000 === 0) {
          console.log(`Processed ${processedPackages.length} packages...`);
        }
        
      } catch (error) {
        console.warn(`Error processing package ${packagePath}:`, error.message);
        continue;
      }
    }

    console.log(`Successfully processed ${processedPackages.length} packages`);
    return processedPackages;
  }

  extractPackageNameFromPath(packagePath) {
    // Extract package name from attribute path (e.g., "nixpkgs.hello" -> "hello")
    const parts = packagePath.split('.');
    return parts[parts.length - 1];
  }

  sanitizeString(str) {
    if (typeof str !== 'string') return '';
    
    // Remove non-printable characters and limit length
    return str
      .replace(/[\x00-\x1F\x7F-\x9F]/g, '') // Remove control characters
      .trim()
      .substring(0, 2000); // Limit to 2000 characters
  }

  extractLicenseInfo(license) {
    if (!license) return '';
    
    if (typeof license === 'string') return this.sanitizeString(license);
    
    if (Array.isArray(license)) {
      return license
        .map(l => typeof l === 'object' ? (l.spdxId || l.fullName || '') : String(l))
        .filter(Boolean)
        .join(', ');
    }
    
    if (typeof license === 'object') {
      return license.spdxId || license.fullName || '';
    }
    
    return String(license).substring(0, 500);
  }

  extractPlatforms(platforms) {
    if (!platforms) return [];
    
    if (Array.isArray(platforms)) {
      return platforms.slice(0, 20); // Limit to first 20 platforms
    }
    
    return [];
  }

  extractMaintainers(maintainers) {
    if (!maintainers) return [];
    
    if (Array.isArray(maintainers)) {
      return maintainers
        .map(m => typeof m === 'object' ? (m.name || m.email || '') : String(m))
        .filter(Boolean)
        .slice(0, 10); // Limit to first 10 maintainers
    }
    
    return [];
  }
}

module.exports = { NixpkgsExtractor };