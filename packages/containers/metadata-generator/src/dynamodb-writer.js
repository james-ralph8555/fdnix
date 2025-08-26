const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, BatchWriteCommand, PutCommand } = require('@aws-sdk/lib-dynamodb');

class DynamoDBWriter {
  constructor(config) {
    this.tableName = config.tableName;
    this.client = DynamoDBDocumentClient.from(
      new DynamoDBClient({ region: config.region })
    );
    this.batchSize = 25; // DynamoDB batch write limit
    this.maxRetries = 3;
    this.baseRetryDelay = 1000; // 1 second
  }

  async batchWritePackages(packages) {
    console.log(`Starting batch write of ${packages.length} packages...`);
    
    let processedCount = 0;
    let errorCount = 0;
    
    // Process packages in batches
    for (let i = 0; i < packages.length; i += this.batchSize) {
      const batch = packages.slice(i, i + this.batchSize);
      
      try {
        await this.writeBatch(batch);
        processedCount += batch.length;
        
        if (processedCount % 100 === 0) {
          console.log(`Written ${processedCount}/${packages.length} packages...`);
        }
        
      } catch (error) {
        console.error(`Failed to write batch ${i}-${i + batch.length}:`, error.message);
        errorCount += batch.length;
        
        // Try to write individual items from failed batch
        for (const pkg of batch) {
          try {
            await this.writeIndividualPackage(pkg);
            processedCount++;
            errorCount--;
          } catch (individualError) {
            console.error(`Failed to write individual package ${pkg.packageName}:`, individualError.message);
          }
        }
      }
      
      // Add small delay to avoid throttling
      if (i + this.batchSize < packages.length) {
        await this.delay(100);
      }
    }
    
    console.log(`Batch write completed. Success: ${processedCount}, Errors: ${errorCount}`);
    
    if (errorCount > 0) {
      console.warn(`Warning: ${errorCount} packages failed to write`);
    }
  }

  async writeBatch(packages) {
    const putRequests = packages.map(pkg => ({
      PutRequest: {
        Item: this.serializePackage(pkg)
      }
    }));

    const params = {
      RequestItems: {
        [this.tableName]: putRequests
      }
    };

    return await this.executeWithRetry(async () => {
      const result = await this.client.send(new BatchWriteCommand(params));
      
      // Handle unprocessed items
      if (result.UnprocessedItems && Object.keys(result.UnprocessedItems).length > 0) {
        console.log(`Retrying ${Object.keys(result.UnprocessedItems[this.tableName] || {}).length} unprocessed items...`);
        
        const retryParams = {
          RequestItems: result.UnprocessedItems
        };
        
        await this.delay(1000); // Wait before retry
        return await this.client.send(new BatchWriteCommand(retryParams));
      }
      
      return result;
    });
  }

  async writeIndividualPackage(pkg) {
    const params = {
      TableName: this.tableName,
      Item: this.serializePackage(pkg)
    };

    return await this.executeWithRetry(async () => {
      return await this.client.send(new PutCommand(params));
    });
  }

  serializePackage(pkg) {
    // Ensure all required attributes are present and properly formatted
    return {
      packageName: pkg.packageName,
      version: pkg.version,
      attributePath: pkg.attributePath || '',
      description: pkg.description || '',
      homepage: pkg.homepage || '',
      license: pkg.license || '',
      platforms: pkg.platforms || [],
      maintainers: pkg.maintainers || [],
      broken: Boolean(pkg.broken),
      unfree: Boolean(pkg.unfree),
      lastUpdated: pkg.lastUpdated,
      hasEmbedding: Boolean(pkg.hasEmbedding)
    };
  }

  async executeWithRetry(operation) {
    let lastError;
    
    for (let attempt = 1; attempt <= this.maxRetries; attempt++) {
      try {
        return await operation();
      } catch (error) {
        lastError = error;
        
        // Check if error is retryable
        if (this.isRetryableError(error) && attempt < this.maxRetries) {
          const delay = this.baseRetryDelay * Math.pow(2, attempt - 1); // Exponential backoff
          console.log(`Attempt ${attempt} failed, retrying in ${delay}ms...`);
          await this.delay(delay);
          continue;
        }
        
        throw error;
      }
    }
    
    throw lastError;
  }

  isRetryableError(error) {
    const retryableErrors = [
      'ProvisionedThroughputExceededException',
      'ThrottlingException',
      'RequestLimitExceeded',
      'InternalServerError',
      'ServiceUnavailable'
    ];
    
    return retryableErrors.some(errorType => 
      error.name === errorType || 
      error.message.includes(errorType)
    );
  }

  delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}

module.exports = { DynamoDBWriter };