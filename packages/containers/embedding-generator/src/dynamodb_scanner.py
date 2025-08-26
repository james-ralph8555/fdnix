import boto3
import logging
import asyncio
from typing import List, Dict, Any, Optional
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Attr

logger = logging.getLogger(__name__)

class DynamoDBScanner:
    def __init__(self, table_name: str, region: str):
        self.table_name = table_name
        self.region = region
        self.dynamodb = boto3.resource('dynamodb', region_name=region)
        self.table = self.dynamodb.Table(table_name)
        self.scan_batch_size = 1000  # DynamoDB scan batch size
        self.max_retries = 3
        self.base_delay = 1.0
        
        logger.info(f"Initialized DynamoDB scanner for table {table_name} in region {region}")

    async def scan_packages_without_embeddings(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Scan DynamoDB for packages that don't have embeddings yet"""
        logger.info("Scanning for packages without embeddings...")
        
        packages = []
        try:
            scan_kwargs = {
                'FilterExpression': Attr('hasEmbedding').eq(False) | Attr('hasEmbedding').not_exists(),
                'ProjectionExpression': 'packageName, version, description, homepage, license, platforms, maintainers, attributePath'
            }
            
            # Use paginator for large scans
            paginator = self.table.meta.client.get_paginator('scan')
            page_iterator = paginator.paginate(
                TableName=self.table_name,
                **scan_kwargs
            )
            
            total_scanned = 0
            total_matched = 0
            
            for page in page_iterator:
                items = page.get('Items', [])
                total_scanned += page.get('ScannedCount', 0)
                
                # Convert DynamoDB items to regular dicts
                for item in items:
                    package = self._deserialize_dynamodb_item(item)
                    packages.append(package)
                    total_matched += 1
                    
                    if limit and len(packages) >= limit:
                        break
                
                if total_matched % 1000 == 0 and total_matched > 0:
                    logger.info(f"Found {total_matched} packages without embeddings (scanned {total_scanned} total)...")
                
                if limit and len(packages) >= limit:
                    break
            
            logger.info(f"Scan completed: found {len(packages)} packages without embeddings (scanned {total_scanned} total)")
            return packages
            
        except Exception as error:
            logger.error(f"Error scanning for packages without embeddings: {str(error)}")
            return []

    async def mark_packages_with_embeddings(self, package_keys: List[Dict[str, str]]) -> bool:
        """Mark packages as having embeddings"""
        logger.info(f"Marking {len(package_keys)} packages as having embeddings...")
        
        try:
            # Update packages in batches using batch_writer
            success_count = 0
            
            with self.table.batch_writer() as batch:
                for package_key in package_keys:
                    try:
                        batch.put_item(
                            Item={
                                'packageName': package_key['packageName'],
                                'version': package_key['version'],
                                'hasEmbedding': True,
                                'embeddingUpdated': asyncio.get_event_loop().time()
                            }
                        )
                        success_count += 1
                    except Exception as error:
                        logger.warning(f"Failed to mark package {package_key}: {str(error)}")
            
            logger.info(f"Successfully marked {success_count}/{len(package_keys)} packages")
            return success_count == len(package_keys)
            
        except Exception as error:
            logger.error(f"Error marking packages with embeddings: {str(error)}")
            return False

    async def get_package_batch(self, package_keys: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """Get a batch of packages by their keys"""
        if not package_keys:
            return []
        
        try:
            # Use batch_get_item for efficient retrieval
            response = await asyncio.to_thread(
                self.dynamodb.meta.client.batch_get_item,
                RequestItems={
                    self.table_name: {
                        'Keys': [
                            {
                                'packageName': {'S': key['packageName']},
                                'version': {'S': key['version']}
                            } for key in package_keys
                        ]
                    }
                }
            )
            
            items = response.get('Responses', {}).get(self.table_name, [])
            packages = [self._deserialize_dynamodb_item(item) for item in items]
            
            return packages
            
        except Exception as error:
            logger.error(f"Error getting package batch: {str(error)}")
            return []

    async def get_packages_needing_update(self, days_old: int = 7) -> List[Dict[str, Any]]:
        """Get packages that might need embedding updates (based on lastUpdated timestamp)"""
        logger.info(f"Scanning for packages that might need embedding updates (older than {days_old} days)...")
        
        import datetime
        cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=days_old)).isoformat()
        
        try:
            packages = []
            scan_kwargs = {
                'FilterExpression': Attr('lastUpdated').lt(cutoff_date) & (
                    Attr('hasEmbedding').eq(False) | 
                    Attr('hasEmbedding').not_exists() |
                    Attr('embeddingUpdated').lt(cutoff_date)
                ),
                'ProjectionExpression': 'packageName, version, lastUpdated, hasEmbedding, embeddingUpdated'
            }
            
            paginator = self.table.meta.client.get_paginator('scan')
            page_iterator = paginator.paginate(
                TableName=self.table_name,
                **scan_kwargs
            )
            
            for page in page_iterator:
                items = page.get('Items', [])
                for item in items:
                    package = self._deserialize_dynamodb_item(item)
                    packages.append(package)
            
            logger.info(f"Found {len(packages)} packages needing updates")
            return packages
            
        except Exception as error:
            logger.error(f"Error scanning for packages needing updates: {str(error)}")
            return []

    async def get_total_package_count(self) -> int:
        """Get total number of packages in the table"""
        try:
            response = await asyncio.to_thread(
                self.table.meta.client.describe_table,
                TableName=self.table_name
            )
            return response['Table']['ItemCount']
        except Exception as error:
            logger.error(f"Error getting total package count: {str(error)}")
            return 0

    async def get_embedding_stats(self) -> Dict[str, int]:
        """Get statistics about embeddings"""
        logger.info("Collecting embedding statistics...")
        
        try:
            total_count = 0
            with_embeddings = 0
            without_embeddings = 0
            
            # Scan the entire table to get accurate counts
            scan_kwargs = {
                'ProjectionExpression': 'hasEmbedding'
            }
            
            paginator = self.table.meta.client.get_paginator('scan')
            page_iterator = paginator.paginate(
                TableName=self.table_name,
                **scan_kwargs
            )
            
            for page in page_iterator:
                items = page.get('Items', [])
                for item in items:
                    total_count += 1
                    has_embedding = item.get('hasEmbedding', {}).get('BOOL', False)
                    if has_embedding:
                        with_embeddings += 1
                    else:
                        without_embeddings += 1
            
            stats = {
                'total_packages': total_count,
                'with_embeddings': with_embeddings,
                'without_embeddings': without_embeddings,
                'embedding_percentage': (with_embeddings / total_count * 100) if total_count > 0 else 0
            }
            
            logger.info(f"Embedding stats: {stats}")
            return stats
            
        except Exception as error:
            logger.error(f"Error collecting embedding stats: {str(error)}")
            return {
                'total_packages': 0,
                'with_embeddings': 0,
                'without_embeddings': 0,
                'embedding_percentage': 0
            }

    def _deserialize_dynamodb_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Convert DynamoDB item format to regular dict"""
        # This handles the low-level DynamoDB format with type descriptors
        result = {}
        
        for key, value in item.items():
            if isinstance(value, dict):
                # Handle DynamoDB type descriptors
                if 'S' in value:  # String
                    result[key] = value['S']
                elif 'N' in value:  # Number
                    try:
                        result[key] = int(value['N'])
                    except ValueError:
                        result[key] = float(value['N'])
                elif 'BOOL' in value:  # Boolean
                    result[key] = value['BOOL']
                elif 'L' in value:  # List
                    result[key] = [self._deserialize_value(v) for v in value['L']]
                elif 'M' in value:  # Map
                    result[key] = self._deserialize_dynamodb_item(value['M'])
                elif 'SS' in value:  # String Set
                    result[key] = list(value['SS'])
                elif 'NS' in value:  # Number Set
                    result[key] = [float(n) for n in value['NS']]
                else:
                    result[key] = value
            else:
                # Already deserialized or simple value
                result[key] = value
        
        return result

    def _deserialize_value(self, value: Dict[str, Any]) -> Any:
        """Deserialize a single DynamoDB value"""
        if 'S' in value:
            return value['S']
        elif 'N' in value:
            try:
                return int(value['N'])
            except ValueError:
                return float(value['N'])
        elif 'BOOL' in value:
            return value['BOOL']
        elif 'L' in value:
            return [self._deserialize_value(v) for v in value['L']]
        elif 'M' in value:
            return self._deserialize_dynamodb_item(value['M'])
        else:
            return value