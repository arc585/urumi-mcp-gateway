<?php
/**
 * Make sure WooCommerce accepts the REST key pair stored in the Kubernetes Secret.
 *
 * WooCommerce normally creates keys in wp-admin and shows them once. We instead generate the
 * pair in a Secret and register it here, so no human has to copy a key, and rotating the Secret
 * and re-running the Job rotates the key.
 *
 * Least privilege: the key belongs to a dedicated "urumi-mcp" user with the shop_manager role
 * (products and orders), not to the WordPress administrator.
 */

$consumer_key    = getenv( 'WC_CONSUMER_KEY' );
$consumer_secret = getenv( 'WC_CONSUMER_SECRET' );
if ( ! $consumer_key || ! $consumer_secret ) {
	WP_CLI::error( 'WC_CONSUMER_KEY and WC_CONSUMER_SECRET must be set.' );
}

$login = 'urumi-mcp';
$user  = get_user_by( 'login', $login );
if ( ! $user ) {
	$user_id = wp_insert_user(
		array(
			'user_login' => $login,
			'user_email' => 'urumi-mcp@example.test',
			'user_pass'  => wp_generate_password( 32 ),
			'role'       => 'shop_manager',
		)
	);
	if ( is_wp_error( $user_id ) ) {
		WP_CLI::error( $user_id->get_error_message() );
	}
	$user = get_user_by( 'id', $user_id );
}

global $wpdb;
$table = $wpdb->prefix . 'woocommerce_api_keys';
$row   = array(
	'user_id'         => $user->ID,
	'description'     => 'urumi-mcp (managed by Helm)',
	'permissions'     => 'read_write',
	// WooCommerce stores a hash of the consumer key and the secret itself, like its own UI does.
	'consumer_key'    => wc_api_hash( $consumer_key ),
	'consumer_secret' => $consumer_secret,
	'truncated_key'   => substr( $consumer_key, -7 ),
);

$key_id = $wpdb->get_var(
	$wpdb->prepare( "SELECT key_id FROM {$table} WHERE user_id = %d LIMIT 1", $user->ID )
);
if ( $key_id ) {
	$wpdb->update( $table, $row, array( 'key_id' => $key_id ) );
} else {
	$wpdb->insert( $table, $row );
}
WP_CLI::success( 'REST API key for user urumi-mcp is in place (ends in ' . $row['truncated_key'] . ').' );
